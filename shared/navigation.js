(() => {
  const defaultLinks = {
    portal: "https://romankhasin.github.io/-level-group-dashboard/portal/",
    dashboard: "https://romankhasin.github.io/-level-group-dashboard/dashboard/",
    reach: "https://romankhasin.github.io/-level-group-dashboard/reach/",
    creative: "https://creative-quality-checker-one.vercel.app/",
    news: "https://romankhasin.github.io/level-realty-radar-new/"
  };

  const settings = Object.assign(
    {
      active: "portal",
      links: defaultLinks
    },
    window.LEVEL_TOOLS_CONFIG || {}
  );

  settings.links = Object.assign({}, defaultLinks, settings.links || {});

  const items = [
    { key: "portal", label: "Главная" },
    { key: "dashboard", label: "Отчёт" },
    { key: "reach", label: "Охваты" },
    { key: "creative", label: "Проверка креативов" },
    { key: "news", label: "Новости" }
  ];

  const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const renderItem = (item) => {
    const href = settings.links[item.key];
    const isActive = settings.active === item.key;
    const dot = `<span class="level-tools-nav__dot" aria-hidden="true"></span>`;

    if (!href) {
      return `<span class="level-tools-nav__link level-tools-nav__link--disabled" aria-disabled="true" title="Раздел готовится">${dot}${escapeHtml(item.label)}</span>`;
    }

    return `<a class="level-tools-nav__link" href="${escapeHtml(href)}"${isActive ? ' aria-current="page"' : ""}>${dot}${escapeHtml(item.label)}</a>`;
  };

  const renderNavigation = (mount) => {
    const active = mount.dataset.active;
    if (active) settings.active = active;

    mount.innerHTML = `
      <nav class="level-tools-nav" aria-label="Навигация по Level Digital Hub">
        <div class="level-tools-nav__inner">
          <a class="level-tools-nav__brand" href="${escapeHtml(settings.links.portal)}" aria-label="Level Digital Hub — главная">
            <span class="level-tools-nav__mark" aria-hidden="true">LG</span>
            <span class="level-tools-nav__brand-copy">
              <span class="level-tools-nav__brand-title">Digital Hub</span>
              <span class="level-tools-nav__brand-subtitle">Level Group tools</span>
            </span>
          </a>
          <div class="level-tools-nav__links">
            ${items.map(renderItem).join("")}
          </div>
        </div>
      </nav>`;
  };

  const injectReachPortalCard = () => {
    const portalMount = document.querySelector('[data-level-navigation][data-active="portal"]');
    const grid = document.querySelector('.tools-grid');
    if (!portalMount || !grid || grid.querySelector('[data-tool="reach"]')) return;

    const card = document.createElement('a');
    card.className = 'tool-card';
    card.dataset.tool = 'reach';
    card.href = settings.links.reach;
    card.style.setProperty('--card-accent', 'rgba(156, 107, 67, 0.16)');
    card.innerHTML = `
      <div class="tool-card__top">
        <span class="tool-card__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/><path d="M4 12H2"/><path d="M22 12h-2"/></svg>
        </span>
        <span class="tool-card__status">Работает</span>
      </div>
      <div class="tool-card__bottom">
        <span class="tool-card__category">Охват и частота</span>
        <h3>Охваты</h3>
        <p class="tool-card__description">Дедуплицированный Reach и Frequency по Level Group, объектам и медиаканалам на основе Device ID из Target Ads.</p>
        <div class="tool-card__features">
          <span class="tool-card__feature">Total Level Group</span>
          <span class="tool-card__feature">По объектам</span>
          <span class="tool-card__feature">По каналам</span>
        </div>
        <span class="tool-card__action">Открыть отчёт <svg viewBox="0 0 24 24"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg></span>
      </div>`;
    grid.appendChild(card);

    const value = document.querySelector('.hero-summary__value');
    const note = document.querySelector('.hero-summary__note');
    const heroCopy = document.querySelector('.hero-copy');
    if (value) value.textContent = '4';
    if (note) note.textContent = 'Четыре рабочих инструмента с общей навигацией.';
    if (heroCopy) heroCopy.textContent = 'Отчёт по размещениям, охваты и частота, проверка креативов и новости рынка — в одной системе.';
  };

  document.querySelectorAll("[data-level-navigation]").forEach(renderNavigation);
  injectReachPortalCard();
})();
