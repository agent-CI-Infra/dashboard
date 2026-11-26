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
    if (!Number.isFinite(start)) return { key: '', label: UNKNOWN, tags: [], color: 0 };
    const prior = releases.filter((row) => row.published <= start).at(-1);
    const changes = releases.filter((row) => row.published > start && row.published < start + DAY);
    const tags = [prior?.tag || UNKNOWN, ...changes.map((row) => row.tag)];
    const last = changes.at(-1) || prior;
    return { key: tags.join('|'), label: tags.join(' → ') + (changes.length ? '（发布日）' : ''),
      tags, color: last ? releases.indexOf(last) % 4 + 1 : 0 };
  }

  function splitPath(path, points) {
    // Only straight time-series lines use this syntax; area bands stay intact.
    if (!/^(?:[ML]-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?)+$/.test(path) || !points.length) return path;
    let previous;
    return path.replace(/([ML])(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (_, command, x, y) => {
      const closest = points.reduce((a, b) => Math.abs(b.x - Number(x)) < Math.abs(a.x - Number(x)) ? b : a);
      const next = command === 'L' && previous !== undefined && previous !== closest.key ? 'M' : command;
      previous = closest.key;
      return `${next}${x},${y}`;
    });
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
      const points = [];
      const segments = [];
      const tags = new Set();
      for (const zone of zones) {
        const current = phase(zone.dataset.date, releases);
        current.tags.forEach((tag) => tags.add(tag));
        zone.dataset.releaseBaseTip ??= zone.dataset.tip;
        zone.dataset.tip = `${zone.dataset.releaseBaseTip}\n发布阶段：${current.label}\n按发布时间标记，非请求实际部署版本`;
        const x = Number(zone.getAttribute('x'));
        const right = x + Number(zone.getAttribute('width'));
        points.push({ x: Number(zone.dataset.x), key: current.key });
        const previous = segments.at(-1);
        if (previous?.key === current.key && Math.abs(previous.right - x) < 0.2) previous.right = right;
        else segments.push({ ...current, x, right, y: zone.getAttribute('y'), height: zone.getAttribute('height') });
      }
      for (const [index, segment] of segments.entries()) {
        bands.append(svgElement('rect', { x: segment.x, y: segment.y, width: segment.right - segment.x,
          height: segment.height, class: `release-band release-color-${segment.color}` }));
        if (index) bands.append(svgElement('line', { x1: segment.x, x2: segment.x, y1: segment.y,
          y2: Number(segment.y) + Number(segment.height), class: 'release-divider' }));
      }
      svg.prepend(bands);
      for (const path of svg.querySelectorAll('path.trend-series')) {
        path.dataset.releaseOriginalPath ??= path.getAttribute('d');
        path.setAttribute('d', splitPath(path.dataset.releaseOriginalPath, points));
      }
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

  globalThis.DigitalMateReleaseTimeline = { normalize, phase, splitPath, decorate };
})();
