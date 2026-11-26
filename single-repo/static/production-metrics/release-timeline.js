/* Release dates describe publication, not the version serving a request. */
(() => {
  'use strict';
  const DAY = 86400000;
  const UNKNOWN = '发布版本未知';

  function normalize(snapshot) {
    const sources = [snapshot?.sections?.ops?.data?.releases?.items, snapshot?.releases];
    const byTag = new Map();
    for (const rows of sources) {
      for (const row of Array.isArray(rows) ? rows : []) {
        const tag = typeof row?.tag === 'string' ? row.tag.trim() : '';
        const published = Date.parse(row?.published_at);
        if (!/^v?\d+\.\d+\.\d+$/.test(tag) || row.draft || row.prerelease
            || !Number.isFinite(published) || byTag.has(tag)) continue;
        byTag.set(tag, { tag, published });
      }
    }
    return [...byTag.values()].sort((a, b) => a.published - b.published || a.tag.localeCompare(b.tag));
  }

  function phase(date, releases) {
    const start = /^\d{4}-\d{2}-\d{2}$/.test(date) ? Date.parse(`${date}T00:00:00+08:00`) : NaN;
    if (!Number.isFinite(start)) return { label: UNKNOWN, tags: [], periods: [], events: [] };
    const prior = releases.filter((row) => row.published <= start).at(-1);
    const changes = releases.filter((row) => row.published > start && row.published < start + DAY);
    const events = releases.filter((row) => row.published >= start && row.published < start + DAY);
    const boundaries = [{ at: start, release: prior }, ...changes.map((release) => ({ at: release.published, release }))];
    const periods = boundaries.map(({ at, release }, index) => ({
      tag: release?.tag || UNKNOWN, published: release?.published,
      from: (at - start) / DAY, to: ((boundaries[index + 1]?.at || start + DAY) - start) / DAY,
      color: release ? releases.indexOf(release) % 4 + 1 : 0,
    })).filter((period) => period.to > period.from);
    const tags = [...new Set(periods.map((period) => period.tag))];
    return { label: tags.join(' → ') + (events.length ? `（当日 ${events.length} 次发布）` : ''),
      tags, periods, events };
  }

  function decorate(root, releases) {
    const doc = root.ownerDocument;
    const svgElement = (tag, attributes) => {
      const element = doc.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
      return element;
    };
    for (const svg of root.querySelectorAll('svg.trend-chart')) {
      const zones = [...svg.querySelectorAll('.hover-zone[data-date]')];
      if (!zones.length) continue;
      svg.querySelector('.release-bands')?.remove();
      svg.previousElementSibling?.matches('.release-legend') && svg.previousElementSibling.remove();
      const bands = svgElement('g', { class: 'release-bands', 'aria-hidden': 'true' });
      const segments = [];
      const tags = new Set();
      for (const zone of zones) {
        const current = phase(zone.dataset.date, releases);
        current.tags.forEach((tag) => tags.add(tag));
        zone.dataset.releaseBaseTip ??= zone.dataset.tip;
        const events = current.events.map((release) => `${release.tag} ${new Date(release.published + 8 * 3600000).toISOString().slice(11, 16)}`).join('；');
        zone.dataset.tip = `${zone.dataset.releaseBaseTip}\n发布阶段：${current.label}`
          + (events ? `\n当日发布（北京时间）：${events}\n日聚合数据无法按发布时刻拆分` : '')
          + '\n按发布时间标记，非请求实际部署版本';
        const x = Number(zone.getAttribute('x'));
        const width = Number(zone.getAttribute('width'));
        for (const period of current.periods) {
          const left = x + width * period.from;
          const right = x + width * period.to;
          const previous = segments.at(-1);
          if (previous?.tag === period.tag && Math.abs(previous.right - left) < 0.2) previous.right = right;
          else segments.push({ ...period, x: left, right, y: zone.getAttribute('y'), height: zone.getAttribute('height') });
        }
      }
      for (const [index, segment] of segments.entries()) {
        bands.append(svgElement('rect', { x: segment.x, y: segment.y, width: segment.right - segment.x,
          height: segment.height, class: `release-band release-color-${segment.color}` }));
        if (index) bands.append(svgElement('line', { x1: segment.x, x2: segment.x, y1: segment.y,
          y2: Number(segment.y) + Number(segment.height), class: 'release-divider',
          'data-release': segment.tag,
          'data-published-at': Number.isFinite(segment.published) ? new Date(segment.published).toISOString() : '' }));
      }
      svg.prepend(bands);
      const legend = doc.createElement('div');
      legend.className = 'release-legend';
      legend.setAttribute('aria-label', '发布版本时间分段');
      const label = doc.createElement('span');
      label.textContent = '发布版本（按发布时间）';
      legend.append(label);
      for (const tag of tags) {
        const index = releases.findIndex((row) => row.tag === tag);
        const chip = doc.createElement('span');
        chip.className = `release-chip release-color-${index < 0 ? 0 : index % 4 + 1}`;
        chip.textContent = tag;
        if (index >= 0) chip.title = `发布于 ${new Date(releases[index].published + 8 * 3600000).toISOString().slice(0, 16).replace('T', ' ')}（Asia/Shanghai）`;
        legend.append(chip);
      }
      svg.before(legend);
    }
  }

  globalThis.DigitalMateReleaseTimeline = { normalize, phase, decorate };
})();
