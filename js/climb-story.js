(function () {
  'use strict';

  var source = document.getElementById('climb-data');
  if (!source) return;

  var data;
  try {
    data = JSON.parse(source.textContent);
  } catch (error) {
    return;
  }
  if (!data || !data.years || !data.years.length) return;

  var BANDS = [
    { key: 'easy', label: 'Easy / 4th', color: '#E8DCC6' },
    { key: '5.0-5.4', label: '5.0–5.4', color: '#D9C39A' },
    { key: '5.5-5.6', label: '5.5–5.6', color: '#C9A874' },
    { key: '5.7', label: '5.7', color: '#D4A574' },
    { key: '5.8', label: '5.8', color: '#C08B5C' },
    { key: '5.9', label: '5.9', color: '#A97448' },
    { key: '5.10', label: '5.10', color: '#8F5D3C' },
    { key: '5.11', label: '5.11', color: '#79472F' },
    { key: '5.12+', label: '5.12+', color: '#5E3324' },
    { key: 'boulder', label: 'Boulders', color: '#8B7355' },
    { key: 'ice', label: 'Ice', color: '#8FA6C9' }
  ];
  var ROPE_BANDS = BANDS.slice(0, 9);
  var STYLE_COLORS = {
    Lead: '#4A7C59',
    TR: '#8FA6C9',
    Follow: '#D4A574',
    Solo: '#C25450',
    Flash: '#4A90A4',
    Unknown: '#BDB7AE'
  };

  var ladder = document.getElementById('climb-ladder');
  var seasons = document.getElementById('climb-seasons');
  var styleBox = document.getElementById('climb-style');
  var regions = document.getElementById('climb-regions');
  var scope = document.getElementById('climb-scope');
  var tooltip = document.getElementById('climb-tooltip');
  if (!ladder || !seasons || !styleBox || !regions || !tooltip) return;

  var hideTimer = null;
  var selectedYear = 'all';

  function cancelHide() {
    if (hideTimer) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide(delay) {
    cancelHide();
    hideTimer = window.setTimeout(function () { tooltip.hidden = true; }, delay || 200);
  }

  function showTip(event, label, count, total) {
    cancelHide();
    var share = total ? Math.round((count / total) * 100) : 0;
    tooltip.innerHTML = '<strong>' + escapeHtml(label) + '</strong><span>' +
      count + ' routes · ' + share + '% of selection</span>';
    tooltip.hidden = false;
    var rect = tooltip.getBoundingClientRect();
    var x = event && typeof event.clientX === 'number' ? event.clientX + 14 : 16;
    var y = event && typeof event.clientY === 'number' ? event.clientY + 14 : 16;
    if (x + rect.width > window.innerWidth - 8) x = window.innerWidth - rect.width - 8;
    if (y + rect.height > window.innerHeight - 8) y = window.innerHeight - rect.height - 8;
    tooltip.style.left = Math.max(8, x) + 'px';
    tooltip.style.top = Math.max(8, y) + 'px';
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }

  function bandFor(grade) {
    var text = String(grade || '').toUpperCase().replace(/\s+/g, '');
    if (/^V\d+/.test(text)) return 'boulder';
    if (/^WI\d+/.test(text)) return 'ice';
    if (text.indexOf('EASY') === 0 || text.indexOf('4TH') === 0) return 'easy';
    var match = text.match(/^5\.(\d+)/);
    if (!match) return 'other';
    var number = parseInt(match[1], 10);
    if (number <= 4) return '5.0-5.4';
    if (number <= 6) return '5.5-5.6';
    if (number === 7) return '5.7';
    if (number === 8) return '5.8';
    if (number === 9) return '5.9';
    if (number === 10) return '5.10';
    if (number === 11) return '5.11';
    return '5.12+';
  }

  function bandsFrom(grades) {
    var totals = {};
    Object.keys(grades || {}).forEach(function (grade) {
      var band = bandFor(grade);
      totals[band] = (totals[band] || 0) + Number(grades[grade] || 0);
    });
    return totals;
  }

  function bandMeta(key) {
    for (var i = 0; i < BANDS.length; i += 1) {
      if (BANDS[i].key === key) return BANDS[i];
    }
    return { key: key, label: key, color: '#BDB7AE' };
  }

  function makeBar(label, count, percent, color, className) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'climb-bar' + (className ? ' ' + className : '');
    button.style.width = Math.min(100, Math.max(count ? 1.5 : 0, percent)).toFixed(2) + '%';
    button.style.backgroundColor = color;
    button.setAttribute('aria-label', label + ': ' + count + ' routes');
    button.dataset.label = label;
    button.dataset.count = String(count);
    return button;
  }

  function renderLadder(totals) {
    ladder.textContent = '';
    var max = 1;
    ROPE_BANDS.forEach(function (band) {
      max = Math.max(max, totals[band.key] || 0);
    });
    ROPE_BANDS.slice().reverse().forEach(function (band) {
      var count = totals[band.key] || 0;
      var row = document.createElement('div');
      row.className = 'climb-ladder__row';

      var label = document.createElement('span');
      label.className = 'climb-ladder__label';
      label.textContent = band.label;

      var track = document.createElement('span');
      track.className = 'climb-ladder__track';
      track.appendChild(makeBar(band.label, count, (count / max) * 100, band.color));

      var value = document.createElement('span');
      value.className = 'climb-ladder__count';
      value.textContent = String(count);

      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      ladder.appendChild(row);
    });
  }

  function renderSeasons(years, selected) {
    seasons.textContent = '';
    var max = 1;
    years.forEach(function (row) { max = Math.max(max, row.ticks); });
    years.forEach(function (row) {
      var totals = bandsFrom(row.grades);
      var line = document.createElement('div');
      line.className = 'climb-seasons__row';

      var yearButton = document.createElement('button');
      yearButton.type = 'button';
      yearButton.className = 'climb-seasons__year';
      yearButton.textContent = String(row.year);
      yearButton.setAttribute('aria-label', 'Focus ' + row.year + ', ' + row.ticks + ' routes');
      yearButton.dataset.year = String(row.year);
      if (String(row.year) === String(selected)) yearButton.classList.add('active');

      var track = document.createElement('span');
      track.className = 'climb-seasons__track';
      var used = 0;
      ROPE_BANDS.concat([BANDS[9], BANDS[10]]).forEach(function (band) {
        var count = totals[band.key] || 0;
        if (!count) return;
        used += count;
        var segment = makeBar(row.year + ' · ' + band.label, count,
          (count / row.ticks) * 100, band.color, 'climb-bar--segment');
        segment.dataset.year = String(row.year);
        track.appendChild(segment);
      });
      if (used < row.ticks) {
        var other = makeBar(row.year + ' · other', row.ticks - used,
          ((row.ticks - used) / row.ticks) * 100, '#BDB7AE', 'climb-bar--segment');
        other.dataset.year = String(row.year);
        track.appendChild(other);
      }

      var count = document.createElement('span');
      count.className = 'climb-seasons__count';
      count.textContent = String(row.ticks);

      line.appendChild(yearButton);
      line.appendChild(track);
      line.appendChild(count);
      seasons.appendChild(line);
    });
  }

  function renderStyles(styles) {
    styleBox.textContent = '';
    var names = Object.keys(styles).sort(function (a, b) { return styles[b] - styles[a]; });
    var total = 0;
    names.forEach(function (name) { total += Number(styles[name] || 0); });
    if (!total) return;

    var track = document.createElement('div');
    track.className = 'climb-style__track';
    names.forEach(function (name) {
      var count = Number(styles[name] || 0);
      var color = STYLE_COLORS[name] || '#BDB7AE';
      track.appendChild(makeBar(name, count, (count / total) * 100, color, 'climb-bar--segment'));
    });

    var legend = document.createElement('div');
    legend.className = 'climb-style__legend';
    names.forEach(function (name) {
      var item = document.createElement('span');
      var dot = document.createElement('i');
      dot.className = 'climb-style__dot';
      dot.style.backgroundColor = STYLE_COLORS[name] || '#BDB7AE';
      item.appendChild(dot);
      item.appendChild(document.createTextNode(name + ' · ' + styles[name]));
      legend.appendChild(item);
    });

    styleBox.appendChild(track);
    styleBox.appendChild(legend);
  }

  function renderRegions(counts) {
    regions.textContent = '';
    var names = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    var max = 1;
    names.forEach(function (name) { max = Math.max(max, counts[name]); });
    names.forEach(function (name) {
      var count = Number(counts[name] || 0);
      var label = name.replace(/-/g, ' ').replace(/\b\w/g, function (letter) {
        return letter.toUpperCase();
      });
      var row = document.createElement('div');
      row.className = 'climb-regions__row';

      var text = document.createElement('span');
      text.className = 'climb-regions__label';
      text.textContent = label;

      var track = document.createElement('span');
      track.className = 'climb-regions__track';
      track.appendChild(makeBar(label, count, (count / max) * 100, '#4A7C59'));

      var value = document.createElement('span');
      value.className = 'climb-regions__count';
      value.textContent = String(count);

      row.appendChild(text);
      row.appendChild(track);
      row.appendChild(value);
      regions.appendChild(row);
    });
  }

  function selection() {
    if (selectedYear === 'all') {
      return {
        grades: data.grades || {},
        styles: data.styles || {},
        regions: data.regions || {},
        years: data.years,
        total: data.metadata && data.metadata.total_ticks ? data.metadata.total_ticks : 0,
        label: 'All years'
      };
    }
    var row = null;
    data.years.forEach(function (candidate) {
      if (String(candidate.year) === String(selectedYear)) row = candidate;
    });
    if (!row) return selection();
    return {
      grades: row.grades || {},
      styles: row.styles || {},
      regions: row.regions || {},
      years: data.years,
      total: row.ticks,
      label: String(row.year)
    };
  }

  function draw() {
    var current = selection();
    if (scope) scope.textContent = current.label + ' · ' + current.total + ' routes';
    renderLadder(bandsFrom(current.grades));
    renderSeasons(current.years, selectedYear);
    renderStyles(current.styles);
    renderRegions(current.regions);
  }

  function wireChart(container) {
    container.addEventListener('mouseover', function (event) {
      var bar = event.target.closest ? event.target.closest('.climb-bar') : null;
      if (!bar) return;
      showTip(event, bar.dataset.label, Number(bar.dataset.count || 0),
        selection().total);
    });
    container.addEventListener('mouseout', function (event) {
      if (event.target.classList && event.target.classList.contains('climb-bar')) scheduleHide(220);
    });
    container.addEventListener('focusin', function (event) {
      if (!event.target.classList || !event.target.classList.contains('climb-bar')) return;
      var rect = event.target.getBoundingClientRect();
      showTip({ clientX: rect.left, clientY: rect.bottom }, event.target.dataset.label,
        Number(event.target.dataset.count || 0), selection().total);
    });
    container.addEventListener('focusout', function () { scheduleHide(220); });
  }

  tooltip.addEventListener('mouseenter', cancelHide);
  tooltip.addEventListener('mouseleave', function () { scheduleHide(120); });

  document.querySelectorAll('.climb-filters [data-year]').forEach(function (button) {
    button.addEventListener('click', function () {
      selectedYear = button.dataset.year;
      document.querySelectorAll('.climb-filters [data-year]').forEach(function (other) {
        var active = other === button;
        other.classList.toggle('active', active);
        other.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      draw();
    });
  });

  [ladder, seasons, styleBox, regions].forEach(wireChart);

  seasons.addEventListener('click', function (event) {
    var target = event.target.closest ? event.target.closest('[data-year]') : null;
    if (!target) return;
    selectedYear = target.dataset.year;
    document.querySelectorAll('.climb-filters [data-year]').forEach(function (button) {
      var active = button.dataset.year === selectedYear;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    draw();
  });

  draw();
})();
