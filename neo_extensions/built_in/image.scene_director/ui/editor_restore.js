// IMG-SD3 + SD-28.9 + IR-5 — Modern regional LoRA-isolation editor + Krea2 external-engine authority.
(function () {
  'use strict';
  const EXTENSION_ID = 'image.scene_director';
  const BUILTIN_MASK_NODES = ['CLIPTextEncode','ConditioningSetMask','ConditioningCombine','ConditioningZeroOut','SolidMask','MaskComposite','FeatherMask'];
  const KREA2_REGIONAL_NODES = ['Krea2RegionalBuilder','Krea2ApplyRegional'];

  function root() { return document.querySelector('[data-scene-director-editor-root]'); }
  function asObject(value) { if (!value) return {}; if (typeof value === 'string') { try { return JSON.parse(value); } catch (_) { return {}; } } return typeof value === 'object' ? value : {}; }
  function clamp(v,min,max){ const n=Number(v); return Number.isFinite(n)?Math.min(max,Math.max(min,n)):min; }
  function uid(){ return `scene_region_${Math.random().toString(36).slice(2,9)}`; }
  function field(r, name){ return r?.querySelector(`[data-sd-field="${name}"]`); }

  const state = {
    enabled: false, display_mode:'guided', prompt_authority:'global_context', global_prompt:'', global_negative:'',
    regions: [], selected_region_id:null,
    route:{backend:'comfyui',family:'sdxl',loader:'checkpoint',mode:'generate'},
    nodeNames:null,
    krea2_regional:{adaptive_masks:'refine boxes',exclusive_masks:true,restrict_img_attn:false,restrict_end_percent:0.5,adaptive_steps:2,adaptive_threshold:0.45,base_loras_exclude_regions:false,region_lock_strength:0.4,region_lock_start:0.35,region_lock_end:0.85,layout_in_base:'position hints',unmaskable_layers:'skip',grow_px:0,feather_px:0},
  };

  function routeFromDom(){
    const r=root(); const host=r?.closest('[data-model-family], [data-family], [data-workflow-mode], [data-image-workspace], [data-surface="image"], #imagePanel, .neo-image-workspace') || document;
    const globalRoute = asObject(window.NeoImageRoute || window.neoImageRoute || window.NeoGenerationRoute);
    const read = (names, selectors=[]) => {
      for (const n of names){ const el=document.querySelector(`[name="${n}"]`); if(el && el.value) return el.value; }
      for (const selector of selectors){ const el=document.querySelector(selector); if(el && el.value) return el.value; }
      return '';
    };
    return {
      backend:String(globalRoute.backend||globalRoute.provider||r?.dataset.backend||read(['backend','provider_id'],['#backend','#providerId','#provider_id'])||'comfyui').toLowerCase(),
      family:globalRoute.family||globalRoute.model_family||r?.dataset.family||host?.dataset?.modelFamily||host?.dataset?.family||read(['family','model_family'],['#imageWorkspaceFamily','#imageFamily','#imageModelFamily','#modelFamily','#model_family','[data-image-field="family"]'])||'sdxl',
      loader:globalRoute.loader||globalRoute.loader_type||r?.dataset.loader||read(['loader','loader_type'],['#imageWorkspaceLoader','#imageLoader','#imageLoaderType','#modelLoader','#loaderType','[data-image-field="loader"]'])||'',
      mode:globalRoute.workflow_mode||globalRoute.mode||r?.dataset.workflowMode||host?.dataset?.workflowMode||read(['workflow_mode','mode'],['#imageWorkflowMode','#imageMode','#workflowMode','[data-image-field="mode"]'])||'generate',
    };
  }

  function namesFrom(value){
    if (!value) return null;
    if (Array.isArray(value)||value instanceof Set) return new Set(Array.from(value).map(String));
    if (typeof value==='object') return new Set(Object.keys(value).concat(Object.values(value).map(v=>v&&typeof v==='object'?(v.class_type||v.name||''):'')).filter(Boolean).map(String));
    return null;
  }

  function isKrea2RegionalRoute(route=state.route){
    const family=String(route?.family||route?.model_family||'').toLowerCase().replace(/[- .]/g,'_');
    return family==='krea2'||family==='krea_2'||family==='krea2_turbo'||family==='krea_2_turbo';
  }

  function readiness(){
    const resolver=window.NeoSceneDirectorRouteAuthority?.resolve;
    if(typeof resolver!=='function') return {route:'unsupported',engine:'route authority unavailable',prompt:'blocked',lora:'blocked',tone:'blocked',reason:'Neo Scene Director live route authority is unavailable. The editor fails closed instead of guessing support.'};
    const resolved=resolver(state.route||{});
    const routeState=String(resolved?.route_state||'unsupported');
    const engine=String(resolved?.engine||'unsupported');
    const engineLabel=String(resolved?.engine_label||engine||'unsupported');
    const nodes=state.nodeNames;
    if(routeState==='provider_gated') return {route:'provider gated',engine:engineLabel,prompt:'blocked',lora:'blocked',tone:'warning',reason:resolved?.reason||'Scene Director execution requires a validated ComfyUI backend.'};
    if(routeState==='planned_gated') return {route:'planned gated',engine:engineLabel,prompt:'blocked',lora:'blocked',tone:'warning',reason:resolved?.reason||'This Scene Director route is planned-gated.'};
    if(routeState==='unsupported') return {route:'unsupported',engine:engineLabel,prompt:'blocked',lora:'blocked',tone:'blocked',reason:resolved?.reason||'No Scene Director execution engine exists for this route.'};
    if(!['available','experimental_available'].includes(routeState)) return {route:routeState.replace(/_/g,' '),engine:engineLabel,prompt:'blocked',lora:'blocked',tone:'warning',reason:resolved?.reason||`Scene Director route state: ${routeState}.`};

    if(engine==='classic_v054'){
      const ok=nodes===null?null:nodes.has('NeoSceneDirectorV054');
      return {route:routeState==='experimental_available'?'experimental':'available',engine:engineLabel,prompt:ok===false?'blocked':'available',lora:ok===false?'runtime missing':'classic V054',tone:ok===false?'warning':(routeState==='experimental_available'?'warning':'ok'),reason:ok===false?'NeoSceneDirectorV054 is missing. The editor stays visible, but classic execution is gated.':(resolved?.reason||'Classic V054 route is available.')};
    }
    if(engine==='lightweight_regional'){
      if(isKrea2RegionalRoute(resolved||state.route)){
        const missing=nodes===null?[]:KREA2_REGIONAL_NODES.filter(n=>!nodes.has(n));
        const ok=nodes===null||missing.length===0;
        return {route:routeState==='experimental_available'?'experimental':'available',engine:'Krea2 Regional',prompt:ok?'available':'blocked',lora:ok?'available':'runtime missing',tone:ok?(routeState==='experimental_available'?'warning':'ok'):'warning',reason:ok?'Krea 2 uses the external ComfyUI-Krea2-Regional engine. Adaptive masks + exclusive masks are the Neo defaults; restrict image attention is optional/off by default.':`Krea 2 Regional runtime is missing: ${missing.join(', ')}. Install/update januspluto/ComfyUI-Krea2-Regional and restart ComfyUI. Neo does not fall back to NeoRegionalLoRADelta for Krea 2.`};
      }
      const requiredBuiltins=String(resolved?.family||'')==='flux2_klein'?BUILTIN_MASK_NODES.concat(['FluxGuidance']):BUILTIN_MASK_NODES;
      const missingBuiltins=nodes===null?[]:requiredBuiltins.filter(n=>!nodes.has(n));
      const promptOk=nodes===null||missingBuiltins.length===0;
      const loraOk=nodes===null?null:nodes.has('NeoRegionalLoRADelta');
      return {route:routeState==='experimental_available'?'experimental':'available',engine:engineLabel,prompt:promptOk?'available':'blocked',lora:loraOk===false?'runtime missing':(loraOk===true?'available':'not checked'),tone:promptOk?(routeState==='experimental_available'?'warning':'ok'):'warning',reason:!promptOk?`Regional prompting is missing Comfy built-ins: ${missingBuiltins.join(', ')}.`:(loraOk===false?'Regional prompting is available. NeoRegionalLoRADelta is missing; generation will fail closed if a regional LoRA row is assigned, without hiding Scene Director.':(resolved?.reason||'Lightweight Regional route is available.'))};
    }
    return {route:'unsupported',engine:engineLabel,prompt:'blocked',lora:'blocked',tone:'blocked',reason:'The live route authority returned no executable Scene Director engine. No fallback is allowed.'};
  }

  function chip(label,value,tone){ return `<span class="neo-scene-editor-chip tone-${tone||'neutral'}"><span>${label}</span><strong>${String(value).replace(/_/g,' ')}</strong></span>`; }
  function renderStatus(){
    const r=root(); if(!r)return; const x=readiness();
    const chips=r.querySelector('[data-sd-status-chips]');
    if(chips) chips.innerHTML=[chip('Route',x.route,x.route==='available'?'ok':x.tone),chip('Engine',x.engine,'neutral'),chip('Regional prompt',x.prompt,x.prompt==='available'?'ok':'warning'),chip('Regional LoRA',x.lora,x.lora==='available'?'ok':(x.lora==='runtime missing'?'warning':'neutral'))].join('');
    const text=r.querySelector('[data-sd-readiness-text]'); if(text) text.textContent=x.reason;
    r.dataset.sceneDirectorRouteState=x.route; r.dataset.sceneDirectorEngine=x.engine;
  }

  function executionEngine(){
    const resolver=window.NeoSceneDirectorRouteAuthority?.resolve;
    if(typeof resolver!=='function') return 'unsupported';
    return String(resolver(state.route||{})?.engine||'unsupported');
  }
  function modernBasicRoute(){ return executionEngine()==='lightweight_regional'; }
  function extensionRoutingCatalog(){
    const catalog=window.NeoSceneDirectorExtensionRouting?.getCatalog?.();
    return catalog&&typeof catalog==='object'?catalog:{options:{lora:[{id:'',label:'None'}],controlnet:[{id:'',label:'None'}],adetailer:[{id:'',label:'None'}],ipadapter:[{id:'',label:'None'}]},lora_rows:[],owner_state:{}};
  }
  function routeSelectedValue(region,kind){
    const routes=asObject(region.extension_routes);
    if(kind==='lora') return String((Array.isArray(routes.lora_row_ids)?routes.lora_row_ids[0]:'')||routes.lora_row_id||'');
    if(kind==='ipadapter') return String(routes.ipadapter_profile_id?`profile:${routes.ipadapter_profile_id}`:(routes.ipadapter_unit_id?`unit:${routes.ipadapter_unit_id}`:''));
    if(kind==='controlnet') return String(routes.controlnet_unit_id||'');
    if(kind==='adetailer') return String(routes.adetailer_pass_id||'');
    return '';
  }
  function routeSelect(kind,region,label){
    const catalog=extensionRoutingCatalog();
    const options=Array.isArray(catalog.options?.[kind])?catalog.options[kind]:[{id:'',label:'None'}];
    const selected=routeSelectedValue(region,kind);
    const fieldName={lora:'extension_routes.lora_row_id',controlnet:'extension_routes.controlnet_unit_id',adetailer:'extension_routes.adetailer_pass_id',ipadapter:'extension_routes.ipadapter_unit_id'}[kind];
    return `<label class="neo-scene-editor-field">${escapeText(label)}<select data-region-field="${fieldName}">${options.map(option=>`<option value="${escapeAttr(option.id||'')}" ${String(option.id||'')===selected?'selected':''}>${escapeText(option.label||option.id||'None')}</option>`).join('')}</select></label>`;
  }
  function extensionRoutingSection(region){
    const modern=modernBasicRoute();
    const catalog=extensionRoutingCatalog();
    const selectedLora=routeSelectedValue(region,'lora');
    const loraRow=(catalog.lora_rows||[]).find(row=>String(row?.uid||'')===selectedLora)||null;
    const stale=selectedLora&&!loraRow?'<p class="neo-scene-editor-runtime-note">The selected regional LoRA row no longer exists in LoRA Stack. Choose an active row before generation.</p>':'';
    const modernNote=modern?(isKrea2RegionalRoute()?'Krea 2 Scene Director delegates regional prompt + LoRA ownership to ComfyUI-Krea2-Regional. The region box is translated into the external Builder; Neo keeps the active loader/sampler/latent/decode path.':'Modern Scene Director is a regional LoRA-isolation layer. The region box owns the LoRA mask; the main prompt/model keeps scene-composition authority. The assigned row is removed from global LoRA execution. Regional prompt text is optional reinforcement.'):'Classic V054 keeps its full repair pipeline; owner extensions still provide the units/rows while Scene Director owns spatial assignment.';
    if(modern){
      return `<section class="neo-scene-region-routing neo-scene-modern-lora-isolation" data-scene-director-extension-routing="basic" data-modern-scene-core="regional_lora_isolation"><div class="neo-scene-region-routing-head"><strong>Regional LoRA Isolation</strong><small>primary modern Scene Director control</small></div><div class="neo-scene-region-row">${routeSelect('lora',region,'LoRA Stack row')}<label class="neo-scene-editor-field">Regional LoRA strength<input data-region-field="lora.strength" type="number" step="0.05" min="-2" max="2" value="${region.lora?.strength??loraRow?.strength??1}"></label></div>${stale}<p class="neo-scene-editor-runtime-note">${modernNote}</p></section>`;
    }
    return `<section class="neo-scene-region-routing" data-scene-director-extension-routing="basic"><div class="neo-scene-region-routing-head"><strong>Extension Routing</strong><small>Spatial owner routing</small></div><div class="neo-scene-region-row">${routeSelect('lora',region,'LoRA Stack row')}<label class="neo-scene-editor-field">Regional LoRA strength<input data-region-field="lora.strength" type="number" step="0.05" min="-2" max="2" value="${region.lora?.strength??loraRow?.strength??1}"></label></div><div class="neo-scene-region-row">${routeSelect('controlnet',region,'ControlNet unit')}${routeSelect('adetailer',region,'ADetailer pass')}</div><div class="neo-scene-region-row">${routeSelect('ipadapter',region,'IP Adapter unit/profile')}<label class="neo-scene-editor-field">Mask mode<select data-region-field="extension_routes.mask_mode">${['region','subject','detail','background'].map(v=>`<option value="${v}" ${String(region.extension_routes?.mask_mode||'region')===v?'selected':''}>${v}</option>`).join('')}</select></label></div>${stale}<p class="neo-scene-editor-runtime-note">${modernNote}</p></section>`;
  }
  function maskHorizontalPosition(region={}){ const b=asObject(region.bbox); const x=clamp(b.x??0,0,1), w=clamp(b.w??.35,.03,1); const center=x+(w/2); return center<=.42?'left':(center>=.58?'right':'center'); }
  function promptHorizontalDirection(prompt=''){ const text=String(prompt||'').toLowerCase().replace(/\s+/g,' '); if(!text.trim())return ''; const patterns={left:[/(?:standing|sitting|seated|positioned|located|placed|walking|posed) (?:on|at|to) the left\b/i,/\bon the left(?: side)?\b(?! of\b)/i],right:[/(?:standing|sitting|seated|positioned|located|placed|walking|posed) (?:on|at|to) the right\b/i,/\bon the right(?: side)?\b(?! of\b)/i],center:[/(?:standing|sitting|seated|positioned|located|placed|walking|posed) (?:in|at) the cent(?:er|re)\b/i,/\bin the cent(?:er|re)\b/i]}; const hits=Object.entries(patterns).filter(([,rows])=>rows.some(rx=>rx.test(text))).map(([key])=>key); return hits.length===1?hits[0]:''; }
  function promptDirectionConflict(region={}){ if(!modernBasicRoute()||String(region.type||region.role||'').toLowerCase()!=='character')return null; const mask=maskHorizontalPosition(region), prompt=promptHorizontalDirection(region.prompt||''); if(!prompt||prompt===mask)return null; return {mask_position:mask,prompt_direction:prompt,message:`${region.label||region.id||'Region'} is masked on the ${mask}, but its prompt says ${prompt}. The mask stays authoritative; Neo will not rewrite the prompt automatically.`}; }

  function applyRouteUiMode(){
    const r=root(); if(!r)return;
    const modern=modernBasicRoute();
    r.dataset.sceneDirectorUiMode=modern?'modern_basic':'classic_full';
    const control=r.querySelector('[data-sd-editor-mode-control]');
    if(control) control.hidden=modern;
    const promptAuthorityControl=r.querySelector('[data-sd-prompt-authority-control]');
    if(promptAuthorityControl) promptAuthorityControl.hidden=modern;
    const kreaControls=r.querySelector('[data-sd-krea2-regional-controls]');
    if(kreaControls) kreaControls.hidden=!isKrea2RegionalRoute();
    if(modern){
      state.display_mode='guided';
      state.prompt_authority='global_context';
      const select=field(r,'display_mode');
      if(select) select.value='guided';
      const promptAuthority=field(r,'prompt_authority');
      if(promptAuthority) promptAuthority.value='global_context';
    }
  }

  function regionTemplate(region,index){
    const selected=state.selected_region_id===region.id;
    const modern=modernBasicRoute();
    const advanced=modern?'':`<details class="neo-scene-region-advanced"><summary>Advanced Region Control</summary><label><input data-region-field="mask.refine_requested" type="checkbox" ${region.mask?.refine_requested?'checked':''}> Request mask refinement</label><div class="neo-scene-region-row"><label class="neo-scene-editor-field">IPAdapter weight<input data-region-field="ipadapter.weight" type="number" step="0.05" min="0" max="2" value="${region.ipadapter?.weight??0.8}"></label></div><p class="neo-scene-editor-runtime-note">Classic V054 retains advanced repair and identity controls. Modern families intentionally omit this layer.</p></details>`;
    const directionConflict=promptDirectionConflict(region);
    const directionWarning=directionConflict?`<p class="neo-scene-editor-runtime-note warning"><strong>⚠ Prompt/mask conflict:</strong> ${escapeText(directionConflict.message)}</p>`:'';
    return `<article class="neo-scene-region-card ${region.enabled===false?'is-inactive':'is-active'}" data-region-id="${region.id}" data-selected="${selected}">
      <div class="neo-scene-region-head"><strong>${region.label||`Region ${index+1}`}</strong><div class="neo-inline-actions"><button type="button" data-sd-region-action="duplicate">Duplicate</button><button type="button" data-sd-region-action="delete">Delete</button></div></div>
      <div class="neo-scene-region-meta-row">
        <label class="neo-scene-editor-field">Label<input data-region-field="label" type="text" value="${escapeAttr(region.label||'')}"></label>
        <label class="neo-scene-editor-field">Type<select data-region-field="type">${['character','object','background','style','text','custom'].map(v=>`<option value="${v}" ${region.type===v?'selected':''}>${v}</option>`).join('')}</select></label>
      </div>
      <div class="neo-scene-region-flag-row"><label><input data-region-field="enabled" type="checkbox" ${region.enabled!==false?'checked':''}> Enabled</label><label><input data-region-field="visible" type="checkbox" ${region.visible!==false?'checked':''}> Visible</label><label><input data-region-field="locked" type="checkbox" ${region.locked?'checked':''}> Locked</label></div>
      <div class="neo-scene-region-bbox-row">${['x','y','w','h'].map(k=>`<label class="neo-scene-editor-field">${k.toUpperCase()}<input data-region-field="bbox.${k}" type="number" min="0" max="1" step="0.01" value="${Number(region.bbox?.[k]??(k==='w'||k==='h'?0.35:0.1)).toFixed(2)}"></label>`).join('')}</div>
      <label class="neo-scene-editor-field">${modern?'Regional prompt · optional reinforcement':'Regional prompt'}<textarea data-region-field="prompt" placeholder="${modern?'Optional: local identity / clothing / pose reinforcement':'Regional prompt'}">${escapeText(region.prompt||'')}</textarea></label>
      ${directionWarning}
      <label class="neo-scene-editor-field">Regional negative<textarea data-region-field="negative_prompt">${escapeText(region.negative_prompt||'')}</textarea></label>
      <div class="neo-scene-region-strength-row"><label class="neo-scene-editor-field">Strength<input data-region-field="strength" type="number" min="0" max="2" step="0.05" value="${region.strength??1}"></label><label class="neo-scene-editor-field">Mask feather<input data-region-field="mask.feather" type="number" min="0" max="128" step="1" value="${region.mask?.feather??16}"></label></div>
      ${extensionRoutingSection(region)}
      ${advanced}
    </article>`;
  }
  function escapeAttr(v){ return String(v??'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function escapeText(v){ return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  function renderCanvas(){ const r=root(); const c=r?.querySelector('[data-sd-canvas]'); if(!c)return; c.innerHTML=''; const visible=state.regions.filter(x=>x.visible!==false); if(!visible.length){c.innerHTML='<div class="neo-scene-editor-canvas-empty">Add a region to preview its normalized position.</div>';return;} visible.forEach(region=>{const b=region.bbox||{};const el=document.createElement('button');el.type='button';el.className=`neo-scene-canvas-region ${region.type||'custom'}${region.locked?' locked-region':''}`;el.dataset.regionId=region.id;el.style.left=`${clamp(b.x,0,1)*100}%`;el.style.top=`${clamp(b.y,0,1)*100}%`;el.style.width=`${clamp(b.w,.03,1)*100}%`;el.style.height=`${clamp(b.h,.03,1)*100}%`;el.innerHTML=`<span>${escapeText(region.label||region.id)}</span><em>${escapeText(region.type||'custom')}</em>`; if(state.selected_region_id===region.id)el.classList.add('selected'); el.addEventListener('click',()=>{state.selected_region_id=region.id;renderRegions();renderCanvas();}); c.appendChild(el);}); }
  function renderRegions(){ const r=root(); const grid=r?.querySelector('[data-sd-regions]'); if(!grid)return; grid.innerHTML=state.regions.map(regionTemplate).join('') || '<p class="neo-scene-inspector-empty">No regions yet. Add a Character, Object, Background, or Style region.</p>'; const count=r.querySelector('[data-sd-region-count]'); if(count)count.textContent=`${state.regions.filter(x=>x.enabled!==false&&x.visible!==false).length} active`; bindRegionEvents(); }

  function newRegion(type){ const i=state.regions.length; return {id:uid(),label:`${type[0].toUpperCase()+type.slice(1)} ${i+1}`,type,enabled:true,visible:true,locked:false,bbox:{x:0.08+(i%4)*0.12,y:0.1+(i%3)*0.09,w:type==='background'?0.84:0.34,h:type==='background'?0.8:0.58},prompt:'',negative_prompt:'',strength:1,mask:{source:'region_box',feather:16,refine_requested:false},extension_routes:{schema:'neo.image.scene_director.extension_unit_routing.v054.v1',lora_row_id:'',controlnet_unit_id:'',adetailer_pass_id:'',ipadapter_unit_id:'',mask_mode:'region'},lora:{strength:1},ipadapter:{weight:.8,use_region_mask:true}}; }

  function bindRegionEvents(){ const r=root(); r?.querySelectorAll('[data-region-id]').forEach(card=>{ const rid=card.dataset.regionId; card.addEventListener('click',e=>{if(e.target.closest('button,input,select,textarea,summary,label'))return; state.selected_region_id=rid;renderRegions();renderCanvas();}); card.querySelectorAll('[data-region-field]').forEach(el=>el.addEventListener('input',()=>updateRegion(rid,el))); card.querySelectorAll('[data-region-field]').forEach(el=>el.addEventListener('change',()=>updateRegion(rid,el))); card.querySelector('[data-sd-region-action="delete"]')?.addEventListener('click',()=>{state.regions=state.regions.filter(x=>x.id!==rid);if(state.selected_region_id===rid)state.selected_region_id=state.regions[0]?.id||null;refresh();}); card.querySelector('[data-sd-region-action="duplicate"]')?.addEventListener('click',()=>{const src=state.regions.find(x=>x.id===rid);if(!src)return;const copy=JSON.parse(JSON.stringify(src));copy.id=uid();copy.label=`${copy.label||'Region'} Copy`;copy.bbox.x=clamp((copy.bbox.x||0)+.04,0,.95);copy.bbox.y=clamp((copy.bbox.y||0)+.04,0,.95);state.regions.push(copy);state.selected_region_id=copy.id;refresh();}); }); }
  function setPath(obj,path,value){const parts=path.split('.');let cur=obj;while(parts.length>1){const k=parts.shift();cur[k]=cur[k]&&typeof cur[k]==='object'?cur[k]:{};cur=cur[k];}cur[parts[0]]=value;}
  function updateRegion(rid,el){ const region=state.regions.find(x=>x.id===rid); if(!region)return; let v=el.type==='checkbox'?el.checked:(el.type==='number'?Number(el.value):el.value); const p=el.dataset.regionField; if(p?.startsWith('bbox.')) v=clamp(v,0,1); setPath(region,p,v); refresh(false); }

  function canonicalBlock(){
    const catalog=extensionRoutingCatalog();
    const rows=Array.isArray(catalog.lora_rows)?catalog.lora_rows:[];
    const regions=state.regions.map((source,index)=>{
      const r=JSON.parse(JSON.stringify(source));
      const routes=asObject(r.extension_routes);
      const rowId=String((Array.isArray(routes.lora_row_ids)?routes.lora_row_ids[0]:'')||routes.lora_row_id||'').trim();
      r.extension_routes={...routes,lora_row_ids:rowId?[rowId]:[],lora_row_id:rowId,mask_mode:String(routes.mask_mode||'region')};
      if(r.lora&&typeof r.lora==='object') delete r.lora.source;
      return r;
    });
    const lora=[];
    regions.forEach((r,i)=>{
      if(r.enabled===false||r.visible===false)return;
      const rowId=String((r.extension_routes?.lora_row_ids||[])[0]||'').trim();
      if(!rowId)return;
      const row=rows.find(item=>String(item?.uid||'')===rowId)||{};
      lora.push({uid:`scene_lora_${rowId}_${r.id}`,lora_row_id:rowId,row_id:rowId,region_id:r.id,region_index:i+1,name:String(row.name||''),lora_name:String(row.name||''),source_record_id:String(row.source_record_id||''),strength:Number(r.lora?.strength??row.strength??1),target:String(row.target||'both'),apply_to:r.id,owner_row:{...row},source:'scene_director_basic_extension_routing'});
    });
    const modern=modernBasicRoute();
    const contracts={enabled:!modern,strict_cast_control:false,use_node_auto_prompts:false,count_contract:'exactly {count} visible subjects, one complete subject per character region, every assigned character region occupied',subject_contract:'exactly one complete visible subject inside this assigned region, separate from neighboring subjects',negative_contract:'fewer than {count} visible subjects, more than {count} visible subjects, missing assigned subject region, merged subjects, shared limbs, fused faces'};
    const promptConflicts=regions.map(r=>promptDirectionConflict(r)).filter(Boolean);
    return {enabled:!!state.enabled,version:1,inputs:{global:{prompt:state.global_prompt||'',negative_prompt:state.global_negative||''},contracts,regions},params:{display_mode:modern?'guided':state.display_mode,prompt_authority:modern?'global_context':state.prompt_authority,strict_cast_control:false,modern_lora_isolation_core:modern,krea2_regional:isKrea2RegionalRoute()?{...state.krea2_regional}:undefined},assets:{lora_bindings:lora,ipadapter_bindings:[]},metadata:{ui_phase:'IMG-SD3+SD-28.9+IR-5',editor_owner:'extension_bundle',ui_mode:modern?'modern_lora_isolation':'classic_full',modern_primary_purpose:modern?'regional_lora_isolation':'classic_scene_director',krea2_engine:isKrea2RegionalRoute()?'krea2_regional_external':'',extension_routing_owner:'basic_region_card',prompt_conflict_policy:'diagnostic_only_no_prompt_mutation',prompt_conflicts:promptConflicts,route:{...state.route}}};
  }
  function sceneGraph(){ const b=canonicalBlock(); return {version:'img-sd1',global:b.inputs.global,regions:b.inputs.regions.map(r=>({id:r.id,role:r.type,type:r.type,label:r.label,bbox:{...r.bbox},prompt:r.prompt||'',negative:r.negative_prompt||'',negative_prompt:r.negative_prompt||'',strength:r.strength,mask:r.mask,extension_routes:r.extension_routes,lora:r.lora,ipadapter:r.ipadapter,enabled:r.enabled,visible:r.visible,locked:r.locked})),metadata:{source:'scene_director_editor',ui_mode:b.metadata.ui_mode,route:{...state.route}}}; }
  function syncHidden(){ const r=root(); if(!r)return; const block=canonicalBlock(); const graph=sceneGraph(); const set=(key,val)=>{const el=r.querySelector(`[data-sd-legacy="${key}"]`);if(el)el.value=typeof val==='string'?val:JSON.stringify(val);}; set('enabled',state.enabled?'true':'false');set('state',block);set('extension',{extensions:{[EXTENSION_ID]:block}});set('scene_graph',graph);set('regions',block.inputs.regions);set('regional_prompts',block.inputs.regions); const pre=r.querySelector('[data-sd-payload-preview]');if(pre)pre.textContent=JSON.stringify({extensions:{[EXTENSION_ID]:block},scene_graph_json:graph},null,2); r.dispatchEvent(new CustomEvent('neo:extension-state-changed',{bubbles:true,detail:{extension_id:EXTENSION_ID,editor_owner:'extension_bundle',source:'extension_bundle',phase:'IR-6.3/SD-28.10A',block,scene_graph_json:graph}})); }
  function refresh(full=true){ state.route=routeFromDom(); applyRouteUiMode(); renderStatus(); if(full){renderRegions();renderCanvas();} else {renderCanvas(); const count=root()?.querySelector('[data-sd-region-count]'); if(count)count.textContent=`${state.regions.filter(x=>x.enabled!==false&&x.visible!==false).length} active`; } syncHidden(); }

  function bind(){ const r=root(); if(!r||r.dataset.sd289Bound==='true')return false; r.dataset.sd289Bound='true'; r.dataset.sceneDirectorEditorOwner='extension_bundle'; r.dataset.sceneDirectorSubmitBridge='IMG-SD1/IR-6.3/SD-28.10A'; const en=field(r,'enabled'); en.checked=state.enabled; en.addEventListener('change',()=>{state.enabled=en.checked;syncHidden();renderStatus();}); ['global_prompt','global_negative','prompt_authority','display_mode'].forEach(name=>{const el=field(r,name); if(!el)return; el.value=state[name]||el.value; el.addEventListener('input',()=>{state[name]=el.value;syncHidden();}); el.addEventListener('change',()=>{state[name]=el.value;syncHidden();});}); r.querySelectorAll('[data-sd-krea2-field]').forEach(el=>{const name=el.dataset.sdKrea2Field;if(!name)return;const current=state.krea2_regional[name];if(el.type==='checkbox')el.checked=Boolean(current);else if(current!==undefined)el.value=String(current);const save=()=>{state.krea2_regional[name]=el.type==='checkbox'?el.checked:(el.type==='number'?Number(el.value):el.value);syncHidden();};el.addEventListener('input',save);el.addEventListener('change',save);}); r.querySelectorAll('[data-sd-add]').forEach(btn=>btn.addEventListener('click',()=>{const reg=newRegion(btn.dataset.sdAdd||'custom');state.regions.push(reg);state.selected_region_id=reg.id;refresh();})); document.addEventListener('change',e=>{ if(e.target?.matches?.('[name="family"],[name="model_family"],[name="loader"],[name="loader_type"],[name="workflow_mode"],[name="mode"],#imageWorkspaceFamily,#imageFamily,#imageModelFamily,#modelFamily,#model_family,#imageWorkspaceLoader,#imageLoader,#imageLoaderType,#modelLoader,#loaderType,#imageWorkflowMode,#imageMode,#workflowMode,[data-image-field="family"],[data-image-field="loader"],[data-image-field="mode"]')) refresh(true); }); document.addEventListener('neo:lora-stack-changed',()=>refresh(true)); refresh(); return true; }

  function hydrate(value){
    const raw=asObject(value); const block=asObject(raw.extensions?.[EXTENSION_ID]||raw[EXTENSION_ID]||raw); const inputs=asObject(block.inputs); const params=asObject(block.params);
    state.enabled=block.enabled!==false; state.global_prompt=String(asObject(inputs.global).prompt||''); state.global_negative=String(asObject(inputs.global).negative_prompt||asObject(inputs.global).negative||''); state.display_mode=String(params.display_mode||'guided'); state.prompt_authority=String(params.prompt_authority||'global_context'); state.krea2_regional={...state.krea2_regional,...asObject(params.krea2_regional)}; state.regions=Array.isArray(inputs.regions)?JSON.parse(JSON.stringify(inputs.regions)):[];
    const rows=extensionRoutingCatalog().lora_rows||[];
    state.regions.forEach(region=>{
      region.extension_routes=asObject(region.extension_routes);
      if(!region.extension_routes.lora_row_id&&!(region.extension_routes.lora_row_ids||[]).length&&region.lora?.source){
        const match=rows.find(row=>String(row?.name||'')===String(region.lora.source||''));
        if(match) region.extension_routes.lora_row_id=String(match.uid||'');
      }
      region.lora={strength:Number(region.lora?.strength??1)};
    });
    state.selected_region_id=state.regions[0]?.id||null; const r=root(); if(r){field(r,'enabled').checked=state.enabled;field(r,'global_prompt').value=state.global_prompt;field(r,'global_negative').value=state.global_negative;field(r,'display_mode').value=state.display_mode;field(r,'prompt_authority').value=state.prompt_authority;} refresh(); return canonicalBlock(); }
  function setRouteContext(route){ state.route={...state.route,...asObject(route)}; const r=root(); if(r){r.dataset.backend=state.route.backend||'';r.dataset.family=state.route.family||'';r.dataset.loader=state.route.loader||'';r.dataset.workflowMode=state.route.mode||state.route.workflow_mode||'';} renderStatus();syncHidden();return readiness(); }
  function setNodeStatus(nodes){ state.nodeNames=namesFrom(nodes);renderStatus();syncHidden();return readiness(); }

  window.NeoSceneDirectorEditor={phase:'IMG-SD3+SD-28.9+IR-5',schema:'neo.image.scene_director.editor.v2',mount:bind,hydrate,getBlock:canonicalBlock,getPayload:()=>({extensions:{[EXTENSION_ID]:canonicalBlock()}}),getSceneGraph:sceneGraph,getReadiness:readiness,isMounted:()=>Boolean(root()?.dataset?.sceneDirectorEditorOwner==='extension_bundle'),setRouteContext,setNodeStatus,serialize:()=>JSON.stringify({extensions:{[EXTENSION_ID]:canonicalBlock()}})};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind,{once:true});else bind();
  document.addEventListener('neo:extensions-mounted',bind);
  // Some extension hosts inject panel.html after DOMContentLoaded without a custom event.
  // Observe only until the editor root appears, then disconnect.
  if (!root() && document.documentElement && window.MutationObserver) {
    const observer = new MutationObserver(() => { if (bind()) observer.disconnect(); });
    observer.observe(document.documentElement, {childList:true, subtree:true});
  }
})();
