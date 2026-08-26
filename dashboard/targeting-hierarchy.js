(() => {
  'use strict';

  const MODE_PROJECT = 'project';
  const MODE_PLATFORM = 'platform';
  const collator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' });

  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const normalize = (value) => clean(value).toLocaleLowerCase('ru-RU');

  function directChild(parent, selector) {
    if (!parent) return null;
    return [...parent.children].find((child) => child.matches?.(selector)) || null;
  }

  function directDetails(parent, className) {
    if (!parent) return [];
    return [...parent.children].filter((child) => child.tagName === 'DETAILS' && child.classList.contains(className));
  }

  function findTree(doc) {
    const byId = doc.getElementById('targeting-tree');
    if (byId) return byId;

    const roots = [...doc.querySelectorAll('details.tc-project')];
    if (!roots.length) return null;
    const likelyRoot = roots.find((node) => !node.parentElement?.closest('details.tc-node'));
    return likelyRoot?.parentElement || roots[0].parentElement;
  }

  function rootProjects(tree) {
    return [...tree.children].filter((node) => (
      node.tagName === 'DETAILS'
      && node.classList.contains('tc-project')
      && !node.classList.contains('tc-project-under-platform')
      && !node.classList.contains('tc-platform-as-root')
    ));
  }

  function summaryOf(detail) {
    return directChild(detail, 'summary');
  }

  function nameElement(detail) {
    return summaryOf(detail)?.querySelector('.tc-name') || null;
  }

  function metaElement(detail) {
    return summaryOf(detail)?.querySelector('.tc-meta') || null;
  }

  function nodeName(detail) {
    return clean(nameElement(detail)?.textContent);
  }

  function nodeMeta(detail) {
    return clean(metaElement(detail)?.textContent);
  }

  function nodeCode(detail) {
    const parts = nodeMeta(detail).split('·').map(clean).filter(Boolean);
    return parts.length >= 2 ? parts[1] : '';
  }

  function childrenContainer(detail) {
    return directChild(detail, '.tc-children')
      || [...detail.children].find((child) => child !== summaryOf(detail) && child.querySelector?.('details.tc-node'))
      || null;
  }

  function directPlatforms(project) {
    const box = childrenContainer(project);
    return directDetails(box, 'tc-platform');
  }

  function directFormats(platform) {
    const box = childrenContainer(platform);
    return directDetails(box, 'tc-format');
  }

  function leafTextElements(cell) {
    return [...cell.querySelectorAll('*')].filter((node) => !node.children.length && clean(node.textContent));
  }

  function metricCellInfo(cell) {
    const leaves = leafTextElements(cell);
    const labelNode = leaves.find((node) => !/[0-9]/.test(clean(node.textContent))) || leaves[0] || null;
    const valueNode = leaves.find((node) => node !== labelNode && /[0-9]/.test(clean(node.textContent)))
      || leaves.find((node) => /[0-9]/.test(clean(node.textContent)))
      || null;
    const label = normalize(labelNode?.textContent || cell.getAttribute('data-label') || '');
    const raw = clean(valueNode?.textContent || '');
    return { cell, label, raw, valueNode };
  }

  function metricCells(detail) {
    const summary = summaryOf(detail);
    if (!summary) return [];
    return [...summary.children]
      .slice(1)
      .map(metricCellInfo)
      .filter((item) => item.label || item.raw);
  }

  function parseNumber(text) {
    const normalized = clean(text)
      .replace(/\u00a0/g, '')
      .replace(/\s/g, '')
      .replace(',', '.')
      .replace(/[^0-9.+-]/g, '');
    const value = Number(normalized);
    return Number.isFinite(value) ? value : 0;
  }

  function parseTime(text) {
    const value = clean(text);
    if (!value.includes(':')) return parseNumber(value);
    const parts = value.split(':').map((item) => Number(item));
    if (parts.some((item) => !Number.isFinite(item))) return 0;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return 0;
  }

  function formatInteger(value) {
    return Math.round(Number(value) || 0).toLocaleString('ru-RU');
  }

  function formatPercent(value) {
    return `${(Number(value) || 0).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
  }

  function formatDecimal(value) {
    return (Number(value) || 0).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  }

  function formatTime(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${m}:${String(s).padStart(2, '0')}`;
  }

  function metricsMap(detail) {
    const map = new Map();
    metricCells(detail).forEach((item) => map.set(item.label, item));
    return map;
  }

  function metricBy(map, patterns) {
    for (const [label, info] of map.entries()) {
      if (patterns.some((pattern) => label.includes(pattern))) return info;
    }
    return null;
  }

  function aggregateMetricText(occurrences, label) {
    const maps = occurrences.map(metricsMap);
    const visits = maps.map((map) => parseNumber(metricBy(map, ['визит'])?.raw));
    const impressions = maps.map((map) => parseNumber(metricBy(map, ['показ'])?.raw));
    const clicks = maps.map((map) => parseNumber(metricBy(map, ['клик'])?.raw));
    const normalizedLabel = normalize(label);

    const matching = maps.map((map) => {
      for (const [key, info] of map.entries()) {
        if (key === normalizedLabel) return info;
      }
      return null;
    });

    if (normalizedLabel.includes('визит')) {
      return formatInteger(visits.reduce((sum, value) => sum + value, 0));
    }
    if (normalizedLabel.includes('показ')) {
      return formatInteger(impressions.reduce((sum, value) => sum + value, 0));
    }
    if (normalizedLabel.includes('клик')) {
      return formatInteger(clicks.reduce((sum, value) => sum + value, 0));
    }
    if (normalizedLabel.includes('отказ')) {
      let weighted = 0;
      let denominator = 0;
      matching.forEach((info, index) => {
        const weight = visits[index] || 0;
        weighted += parseNumber(info?.raw) * weight;
        denominator += weight;
      });
      return formatPercent(denominator ? weighted / denominator : 0);
    }
    if (normalizedLabel.includes('время')) {
      let weighted = 0;
      let denominator = 0;
      matching.forEach((info, index) => {
        const weight = visits[index] || 0;
        weighted += parseTime(info?.raw) * weight;
        denominator += weight;
      });
      return formatTime(denominator ? weighted / denominator : 0);
    }
    if (normalizedLabel.includes('ctr')) {
      const totalImpressions = impressions.reduce((sum, value) => sum + value, 0);
      const totalClicks = clicks.reduce((sum, value) => sum + value, 0);
      if (totalImpressions && totalClicks) return formatPercent(totalClicks / totalImpressions * 100);
      let weighted = 0;
      let denominator = 0;
      matching.forEach((info, index) => {
        const weight = impressions[index] || 0;
        weighted += parseNumber(info?.raw) * weight;
        denominator += weight;
      });
      return formatPercent(denominator ? weighted / denominator : 0);
    }
    if (normalizedLabel.includes('ivt')) {
      let weighted = 0;
      let denominator = 0;
      matching.forEach((info, index) => {
        const weight = impressions[index] || 0;
        weighted += parseNumber(info?.raw) * weight;
        denominator += weight;
      });
      return formatPercent(denominator ? weighted / denominator : 0);
    }
    if (normalizedLabel.includes('эфф') || normalizedLabel.includes('показ') && normalizedLabel.includes('визит')) {
      const totalVisits = visits.reduce((sum, value) => sum + value, 0);
      const totalImpressions = impressions.reduce((sum, value) => sum + value, 0);
      return formatDecimal(totalVisits ? totalImpressions / totalVisits : 0);
    }

    const values = matching.map((info) => parseNumber(info?.raw));
    if (matching.some((info) => String(info?.raw || '').includes('%'))) {
      return formatPercent(values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length));
    }
    return formatInteger(values.reduce((sum, value) => sum + value, 0));
  }

  function stripAggregateTrends(summary) {
    summary?.querySelectorAll('canvas,svg,.sparkline,.tc-sparkline,[class*="spark"],[class*="trend"]').forEach((node) => node.remove());
  }

  function applyAggregateMetrics(root, occurrences) {
    const summary = summaryOf(root);
    if (!summary || !occurrences.length) return;
    stripAggregateTrends(summary);
    metricCells(root).forEach((info) => {
      if (!info.valueNode || !info.label) return;
      info.valueNode.textContent = aggregateMetricText(occurrences, info.label);
    });
  }

  function setNodeIdentity(detail, { name, type, code, count, countLabel }) {
    const nameEl = nameElement(detail);
    const metaEl = metaElement(detail);
    if (nameEl) nameEl.textContent = name || 'Без названия';
    if (metaEl) {
      const safeCode = clean(code) || '—';
      const suffix = countLabel || 'ЭЛЕМЕНТ(А)';
      metaEl.textContent = `${type} · ${safeCode} · ${count} ${suffix}`;
    }
    detail.dataset.search = normalize(`${name} ${code} ${type} ${detail.textContent}`);
  }

  function cloneProjectInsidePlatform(project, platform) {
    const clone = platform.cloneNode(true);
    clone.open = false;
    clone.classList.remove('tc-platform', 'tc-platform-as-root');
    clone.classList.add('tc-project', 'tc-project-under-platform');
    setNodeIdentity(clone, {
      name: nodeName(project),
      type: 'ЖК',
      code: nodeCode(project),
      count: directFormats(platform).length,
      countLabel: 'ЭЛЕМЕНТ(А)'
    });
    return clone;
  }

  function buildPlatformTree(tree) {
    const projects = rootProjects(tree);
    const groups = new Map();

    projects.forEach((project) => {
      directPlatforms(project).forEach((platform) => {
        const name = nodeName(platform) || 'Не определено';
        const code = nodeCode(platform);
        const key = normalize(code || name);
        if (!groups.has(key)) groups.set(key, { name, code, occurrences: [] });
        groups.get(key).occurrences.push({ project, platform });
      });
    });

    const fragment = tree.ownerDocument.createDocumentFragment();
    [...groups.values()]
      .sort((a, b) => collator.compare(a.name, b.name))
      .forEach((group) => {
        const source = group.occurrences[0].platform;
        const root = source.cloneNode(true);
        root.open = false;
        root.classList.remove('tc-platform', 'tc-project-under-platform');
        root.classList.add('tc-project', 'tc-platform-as-root');

        const children = childrenContainer(root);
        if (children) {
          children.replaceChildren(...group.occurrences
            .sort((a, b) => collator.compare(nodeName(a.project), nodeName(b.project)))
            .map(({ project, platform }) => cloneProjectInsidePlatform(project, platform)));
        }

        setNodeIdentity(root, {
          name: group.name,
          type: 'ПЛОЩАДКА',
          code: group.code,
          count: group.occurrences.length,
          countLabel: 'ЖК'
        });
        applyAggregateMetrics(root, group.occurrences.map(({ platform }) => platform));
        fragment.appendChild(root);
      });

    return fragment;
  }

  function closeAll(tree) {
    tree.querySelectorAll('details').forEach((node) => { node.open = false; });
  }

  function findSubtitle(doc, tree) {
    const scope = tree.closest('#targeting-panel,section,.panel') || doc;
    return [...scope.querySelectorAll('p')].find((node) => clean(node.textContent).startsWith('Иерархия:')) || null;
  }

  function updateSubtitle(subtitle, mode, originalText) {
    if (!subtitle) return;
    const hierarchy = mode === MODE_PLATFORM
      ? 'Иерархия: Площадка → ЖК → Формат → Аудиторный сегмент → Креатив'
      : 'Иерархия: ЖК → Площадка → Формат → Аудиторный сегмент → Креатив';
    const base = originalText || clean(subtitle.textContent);
    subtitle.textContent = /^Иерархия:[^.]*\./.test(base)
      ? base.replace(/^Иерархия:[^.]*\./, `${hierarchy}.`)
      : `${hierarchy}. ${base.replace(/^Иерархия:[^.]*\.?\s*/, '')}`;
  }

  function injectStyles(doc) {
    if (doc.getElementById('targeting-hierarchy-switch-style')) return;
    const style = doc.createElement('style');
    style.id = 'targeting-hierarchy-switch-style';
    style.textContent = `
      .tc-hierarchy-switch{display:inline-flex;align-items:center;gap:4px;margin:0 0 14px;padding:4px;border:1px solid rgba(156,107,67,.18);border-radius:999px;background:rgba(247,244,239,.78)}
      .tc-hierarchy-switch__button{min-height:38px;padding:0 16px;border:0;border-radius:999px;background:transparent;color:var(--muted,#747782);font:800 11px/1 Montserrat,sans-serif;cursor:pointer;transition:.18s ease}
      .tc-hierarchy-switch__button:hover{color:var(--ink,#20233A);background:rgba(255,255,255,.72)}
      .tc-hierarchy-switch__button.is-active{color:var(--ink,#20233A);background:#fff;box-shadow:0 5px 16px rgba(32,35,58,.09)}
      .tc-platform-as-root{margin-left:0!important;border-left:5px solid var(--gold,#C7A05A)!important}
      .tc-project-under-platform{margin-left:18px!important;border-left:4px solid rgba(89,145,180,.34)!important}
      .tc-project-under-platform > summary .tc-meta{color:var(--muted,#747782)}
      @media(max-width:720px){.tc-hierarchy-switch{display:flex;width:100%}.tc-hierarchy-switch__button{flex:1;padding:0 10px}}
    `;
    doc.head.appendChild(style);
  }

  function placeSwitcher(doc, tree, onChange) {
    const existing = doc.getElementById('targeting-hierarchy-switch');
    if (existing) return existing;

    const switcher = doc.createElement('div');
    switcher.id = 'targeting-hierarchy-switch';
    switcher.className = 'tc-hierarchy-switch';
    switcher.setAttribute('role', 'group');
    switcher.setAttribute('aria-label', 'Порядок группировки дерева кампаний');
    switcher.innerHTML = `
      <button class="tc-hierarchy-switch__button is-active" type="button" data-mode="${MODE_PROJECT}">По ЖК</button>
      <button class="tc-hierarchy-switch__button" type="button" data-mode="${MODE_PLATFORM}">По площадкам</button>
    `;
    switcher.addEventListener('click', (event) => {
      const button = event.target.closest('[data-mode]');
      if (button) onChange(button.dataset.mode);
    });

    const scope = tree.closest('#targeting-panel,section,.panel') || tree.parentElement;
    const searchInput = scope?.querySelector('input[type="search"]');
    const searchRow = searchInput?.closest('.targeting-search,.tc-search,.search-row,.toolbar') || searchInput?.parentElement;
    if (searchRow && searchRow !== tree) searchRow.insertAdjacentElement('afterend', switcher);
    else tree.parentElement?.insertBefore(switcher, tree);
    return switcher;
  }

  function install(frame) {
    let doc;
    try { doc = frame.contentDocument; } catch (_) { return false; }
    if (!doc?.documentElement) return false;

    const tree = findTree(doc);
    if (!tree || !rootProjects(tree).length) return false;
    if (doc.documentElement.dataset.targetingHierarchyInstalled === '1') return true;
    doc.documentElement.dataset.targetingHierarchyInstalled = '1';

    injectStyles(doc);
    let mode = MODE_PROJECT;
    let internalMutation = false;
    let projectHtml = tree.innerHTML;
    const subtitle = findSubtitle(doc, tree);
    const originalSubtitle = clean(subtitle?.textContent);

    const renderMode = (nextMode) => {
      if (![MODE_PROJECT, MODE_PLATFORM].includes(nextMode)) return;
      mode = nextMode;
      internalMutation = true;
      try {
        if (mode === MODE_PROJECT) {
          tree.innerHTML = projectHtml;
        } else {
          if (!tree.querySelector('.tc-platform-as-root')) projectHtml = tree.innerHTML;
          const staging = doc.createElement('div');
          staging.innerHTML = projectHtml;
          tree.replaceChildren(buildPlatformTree(staging));
        }
        closeAll(tree);
        const search = (tree.closest('#targeting-panel,section,.panel') || doc).querySelector('input[type="search"]');
        if (search?.value) search.dispatchEvent(new Event('input', { bubbles: true }));
      } finally {
        internalMutation = false;
      }

      const switcher = doc.getElementById('targeting-hierarchy-switch');
      switcher?.querySelectorAll('[data-mode]').forEach((button) => {
        const active = button.dataset.mode === mode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      updateSubtitle(subtitle, mode, originalSubtitle);
    };

    placeSwitcher(doc, tree, renderMode);
    renderMode(MODE_PROJECT);

    let refreshTimer = null;
    const observer = new MutationObserver(() => {
      if (internalMutation) return;
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        if (internalMutation) return;
        if (mode === MODE_PROJECT) {
          if (rootProjects(tree).length) projectHtml = tree.innerHTML;
        } else if (!tree.querySelector('.tc-platform-as-root') && rootProjects(tree).length) {
          projectHtml = tree.innerHTML;
          renderMode(MODE_PLATFORM);
        }
      }, 120);
    });
    observer.observe(tree, { childList: true, subtree: false });
    return true;
  }

  function start() {
    const frame = document.querySelector('iframe.dashboard-frame');
    if (!frame) return;
    const tryInstall = () => {
      let attempts = 0;
      const timer = setInterval(() => {
        attempts += 1;
        if (install(frame) || attempts > 180) clearInterval(timer);
      }, 250);
    };
    frame.addEventListener('load', tryInstall);
    if (frame.contentDocument?.readyState === 'complete' || frame.contentDocument?.readyState === 'interactive') tryInstall();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
