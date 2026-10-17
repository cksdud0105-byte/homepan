// Explicit user selection. Capture prevents a picked publish button firing.
({label, kind}) => {
  if (window.__homepanPickCleanup) window.__homepanPickCleanup();
  window.__homepanPicked = null;
  const banner = document.createElement('div');
  banner.dataset.homepanOverlay = '1';
  banner.textContent = label + ' | 마우스로 가리키고 ↑로 상위 영역 선택, 클릭으로 확정, Esc 취소';
  Object.assign(banner.style, {position:'fixed', top:'0', left:'0', right:'0', zIndex:'2147483647',
    padding:'12px', background:'#163626', color:'white', fontSize:'15px', pointerEvents:'none'});
  const box = document.createElement('div');
  box.dataset.homepanOverlay = '1';
  Object.assign(box.style, {position:'fixed', zIndex:'2147483646', border:'3px solid #12b886',
    pointerEvents:'none', boxSizing:'border-box'});
  document.documentElement.append(banner, box);
  let selected = null;
  function paint() {
    if (!selected) return;
    const r = selected.getBoundingClientRect();
    Object.assign(box.style, {left:r.left+'px', top:r.top+'px', width:r.width+'px', height:r.height+'px'});
  }
  function selector(el) {
    const path = [];
    while (el && el.nodeType === 1) {
      if (el.id) {
        const q = '#' + CSS.escape(el.id);
        if (document.querySelectorAll(q).length === 1) { path.unshift(q); break; }
      }
      let part = el.tagName.toLowerCase();
      const siblings = el.parentElement ? [...el.parentElement.children].filter(x => x.tagName === el.tagName) : [];
      if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el)+1) + ')';
      path.unshift(part);
      el = el.parentElement;
    }
    return path.join(' > ');
  }
  function normalize(el) {
    if (kind === 'editable') return el.closest('textarea,input,[contenteditable="true"]');
    if (kind === 'button') return el.closest('button,[role="button"],a,input[type="button"],input[type="submit"]');
    return el;
  }
  function move(e) {
    if (e.target?.dataset?.homepanOverlay) return;
    selected = normalize(e.target);
    paint();
  }
  function key(e) {
    if (e.key === 'Escape') { e.preventDefault(); window.__homepanPicked={cancelled:true}; cleanup(); }
    if (e.key === 'ArrowUp' && kind === 'region' && selected?.parentElement) {
      e.preventDefault(); e.stopImmediatePropagation(); selected=selected.parentElement; paint();
    }
  }
  function click(e) {
    e.preventDefault(); e.stopImmediatePropagation();
    if (!selected) return;
    if (selected.matches('input[type="password"],input[type="file"]')) return;
    window.__homepanPicked = {selector:selector(selected), tag:selected.tagName.toLowerCase(),
      role:selected.getAttribute('role') || '', editable:selected.isContentEditable,
      type:selected.getAttribute('type') || ''};
    cleanup();
  }
  function suppress(e) { e.preventDefault(); e.stopImmediatePropagation(); }
  function cleanup() {
    for (const [event, handler] of [['mousemove',move],['keydown',key],['click',click],['pointerdown',suppress],['mousedown',suppress],['pointerup',suppress],['mouseup',suppress]])
      window.removeEventListener(event, handler, true);
    banner.remove(); box.remove(); delete window.__homepanPickCleanup;
  }
  for (const [event, handler] of [['mousemove',move],['keydown',key],['click',click],['pointerdown',suppress],['mousedown',suppress],['pointerup',suppress],['mouseup',suppress]])
    window.addEventListener(event, handler, true);
  window.__homepanPickCleanup = cleanup;
}
