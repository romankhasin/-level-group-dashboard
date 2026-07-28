(() => {
  const defaultLinks = {
    portal: "https://romankhasin.github.io/-level-group-dashboard/portal/",
    dashboard: "https://romankhasin.github.io/-level-group-dashboard/dashboard/",
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

  document.querySelectorAll("[data-level-navigation]").forEach(renderNavigation);
})();
