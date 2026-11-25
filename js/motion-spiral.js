/*
 * Year in Motion
 * ---------------------------------------------------------------------------
 * Draws a radial calendar from data/insights/daily.json. Empty days are not
 * drawn: each petal is one logged day, coloured by sport, with radial length
 * for distance and tip glow for elevation.
 */
(function () {
  'use strict';

  var COLORS = {
    bike: '#C25450',
    climb: '#D4A574',
    hike: '#4A7C59',
    run: '#4A90A4',
    ski: '#8FA6C9',
    water: '#0C589C'
  };

  var LABELS = {
    bike: 'Bike',
    climb: 'Climb',
    hike: 'Hike',
    run: 'Run',
    ski: 'Ski',
    water: 'Water',
    other: 'Activity'
  };

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var SIZE = 900;
  var CENTER = SIZE / 2;
  var INNER = 66;
  var OUTER = 402;

  var root = document.getElementById('motion-spiral');
  var dataNode = document.getElementById('motion-data');
  var tooltip = document.getElementById('motion-tooltip');
  var scaleToggle = document.getElementById('motion-year-scale');
  if (!root || !dataNode || !tooltip) return;

  var days = [];
  try {
    days = JSON.parse(dataNode.textContent || '[]') || [];
  } catch (error) {
    root.textContent = 'Activity data could not be read.';
    return;
  }

  if (!days.length) {
    root.innerHTML = '<p class="motion-empty">No activity days are available yet.</p>';
    return;
  }

  var baseurl = window.MOTION_BASEURL || '';
  var filter = 'all';
  var perYearScale = false;
  var hideTimer = null;

  function cancelHide() {
    if (hideTimer) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide(delay) {
    cancelHide();
    hideTimer = window.setTimeout(hideTooltip, delay || 180);
  }

  function svg(tag, attributes) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attributes || {}).forEach(function (key) {
      node.setAttribute(key, attributes[key]);
    });
    return node;
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function daysInYear(year) {
    return ((year % 4 === 0 && year % 100 !== 0) || year % 400 === 0) ? 366 : 365;
  }

  function sectorPath(cx, cy, r0, r1, start, end) {
    var large = end - start > Math.PI ? 1 : 0;
    var p = function (radius, angle) {
      return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
    };
    var a = p(r0, start);
    var b = p(r1, start);
    var c = p(r1, end);
    var d = p(r0, end);
    return [
      'M', a[0].toFixed(2), a[1].toFixed(2),
      'L', b[0].toFixed(2), b[1].toFixed(2),
      'A', r1.toFixed(2), r1.toFixed(2), 0, large, 1, c[0].toFixed(2), c[1].toFixed(2),
      'L', d[0].toFixed(2), d[1].toFixed(2),
      'A', r0.toFixed(2), r0.toFixed(2), 0, large, 0, a[0].toFixed(2), a[1].toFixed(2),
      'Z'
    ].join(' ');
  }

  function maxDistanceFor(year) {
    var max = 0;
    days.forEach(function (day) {
      if (perYearScale && day.year !== year) return;
      Object.keys(day.sports || {}).forEach(function (sport) {
        if (filter !== 'all' && sport !== filter) return;
        max = Math.max(max, Number((day.sports[sport] || {}).distance_mi) || 0);
      });
    });
    return max || 1;
  }

  function maxElevationFor(year) {
    var max = 0;
    days.forEach(function (day) {
      if (perYearScale && day.year !== year) return;
      Object.keys(day.sports || {}).forEach(function (sport) {
        if (filter !== 'all' && sport !== filter) return;
        max = Math.max(max, Number((day.sports[sport] || {}).elevation_ft) || 0);
      });
    });
    return max || 1;
  }

  function showTooltip(event, day, sport, values) {
    cancelHide();
    var distance = Number(values.distance_mi || 0).toFixed(1);
    var elevation = Math.round(Number(values.elevation_ft || 0)).toLocaleString('en-US');
    var label = LABELS[sport] || sport;
    tooltip.innerHTML =
      '<strong>' + escapeHtml(day.date) + '</strong>' +
      '<span>' + escapeHtml(label) + ' &middot; ' + distance + ' mi &middot; ' + elevation + ' ft</span>' +
      '<a href="' + escapeHtml(baseurl + '/?activity=' + encodeURIComponent(sport)) + '">' +
      'Explore ' + escapeHtml(label) + ' in the atlas</a>';
    tooltip.hidden = false;

    var x = event.clientX;
    var y = event.clientY;
    if (!x && event.target.getBoundingClientRect) {
      var box = event.target.getBoundingClientRect();
      x = box.left + box.width / 2;
      y = box.top;
    }
    var width = tooltip.offsetWidth || 220;
    var height = tooltip.offsetHeight || 80;
    tooltip.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, x + 14)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(window.innerHeight - height - 8, y - height - 12)) + 'px';
  }

  function hideTooltip() {
    cancelHide();
    tooltip.hidden = true;
  }

  function render() {
    while (root.firstChild) root.removeChild(root.firstChild);
    hideTooltip();

    var years = [];
    days.forEach(function (day) {
      if (day.year && years.indexOf(day.year) === -1) years.push(day.year);
    });
    years.sort(function (a, b) { return a - b; });
    if (!years.length) {
      root.textContent = 'No dated activity days are available yet.';
      return;
    }

    var ringStep = Math.min(46, (OUTER - INNER) / years.length);
    var canvas = svg('svg', {
      viewBox: '0 0 ' + SIZE + ' ' + SIZE,
      xmlns: SVG_NS,
      'aria-hidden': 'true',
      focusable: 'false'
    });

    // Faint month spokes make the calendar legible without a full grid.
    var spokeGroup = svg('g', { class: 'motion-spokes' });
    for (var month = 0; month < 12; month += 1) {
      var angle = -Math.PI / 2 + month / 12 * Math.PI * 2;
      var endRadius = INNER + ringStep * years.length;
      spokeGroup.appendChild(svg('line', {
        x1: (CENTER + INNER * Math.cos(angle)).toFixed(2),
        y1: (CENTER + INNER * Math.sin(angle)).toFixed(2),
        x2: (CENTER + endRadius * Math.cos(angle)).toFixed(2),
        y2: (CENTER + endRadius * Math.sin(angle)).toFixed(2)
      }));
      if (month % 3 === 0) {
        var label = svg('text', {
          x: (CENTER + (endRadius + 18) * Math.cos(angle)).toFixed(2),
          y: (CENTER + (endRadius + 18) * Math.sin(angle)).toFixed(2),
          class: 'motion-month'
        });
        label.textContent = ['Jan', 'Apr', 'Jul', 'Oct'][month / 3];
        spokeGroup.appendChild(label);
      }
    }
    canvas.appendChild(spokeGroup);

    // Year rings, oldest inside, most recent outside.
    years.forEach(function (year, index) {
      var radius = INNER + index * ringStep;
      canvas.appendChild(svg('circle', {
        cx: CENTER,
        cy: CENTER,
        r: radius.toFixed(2),
        class: 'motion-ring'
      }));
      var label = svg('text', {
        x: CENTER + 6,
        y: (CENTER - radius - 6).toFixed(2),
        class: 'motion-year-label'
      });
      label.textContent = year;
      canvas.appendChild(label);
    });

    var petalGroup = svg('g', { class: 'motion-petals' });
    days.forEach(function (day) {
      var yearIndex = years.indexOf(day.year);
      if (yearIndex === -1) return;
      var sports = Object.keys(day.sports || {}).filter(function (sport) {
        return filter === 'all' || sport === filter;
      });
      if (!sports.length) return;

      var maxDistance = maxDistanceFor(day.year);
      var maxElevation = maxElevationFor(day.year);
      var totalDays = daysInYear(day.year);
      var wedge = Math.PI * 2 / totalDays * 0.84;
      var subWedge = wedge / sports.length;
      var baseRadius = INNER + yearIndex * ringStep + 2;

      sports.forEach(function (sport, sportIndex) {
        var values = day.sports[sport] || {};
        var distance = Number(values.distance_mi || 0);
        var elevation = Number(values.elevation_ft || 0);
        var radial = distance > 0
          ? 5 + Math.pow(distance / maxDistance, 0.72) * ringStep * 0.76
          : Math.max(3, ringStep * 0.12);
        var start = -Math.PI / 2 + (Number(day.day_of_year) - 1) / totalDays * Math.PI * 2
          - wedge / 2 + sportIndex * subWedge;
        var end = start + subWedge * 0.88;
        var r0 = baseRadius;
        var r1 = Math.min(OUTER, baseRadius + radial);
        var color = COLORS[sport] || COLORS.other;
        var opacity = 0.52 + Math.min(1, elevation / maxElevation) * 0.44;

        var path = svg('path', {
          d: sectorPath(CENTER, CENTER, r0, r1, start, end),
          fill: color,
          'fill-opacity': opacity.toFixed(2),
          stroke: color,
          'stroke-width': '0.45',
          class: 'motion-petal motion-petal--' + sport,
          tabindex: '0',
          role: 'button',
          'aria-label': day.date + ', ' + (LABELS[sport] || sport) + ', ' +
            distance.toFixed(1) + ' miles, ' + Math.round(elevation) + ' feet'
        });
        path.dataset.date = day.date;
        path.dataset.sport = sport;
        path.dataset.distance = String(distance);
        path.dataset.elevation = String(elevation);
        petalGroup.appendChild(path);

        if (elevation > 0 && radial > 6) {
          var tipAngle = (start + end) / 2;
          var tipRadius = Math.min(OUTER, r1 - 1);
          petalGroup.appendChild(svg('circle', {
            cx: (CENTER + tipRadius * Math.cos(tipAngle)).toFixed(2),
            cy: (CENTER + tipRadius * Math.sin(tipAngle)).toFixed(2),
            r: (1.1 + Math.min(1, elevation / maxElevation) * 2.1).toFixed(2),
            fill: color,
            'fill-opacity': '0.85',
            class: 'motion-glow'
          }));
        }
      });
    });
    canvas.appendChild(petalGroup);

    var hub = svg('g', { class: 'motion-hub' });
    hub.appendChild(svg('circle', { cx: CENTER, cy: CENTER, r: INNER - 8, class: 'motion-hub-bg' }));
    var line1 = svg('text', { x: CENTER, y: CENTER - 10, class: 'motion-hub-title' });
    line1.textContent = 'YEAR IN';
    var line2 = svg('text', { x: CENTER, y: CENTER + 12, class: 'motion-hub-title' });
    line2.textContent = 'MOTION';
    var yearsText = svg('text', { x: CENTER, y: CENTER + 32, class: 'motion-hub-years' });
    yearsText.textContent = years[0] + '\u2013' + years[years.length - 1];
    hub.appendChild(line1);
    hub.appendChild(line2);
    hub.appendChild(yearsText);
    canvas.appendChild(hub);

    root.appendChild(canvas);
  }

  root.addEventListener('mouseover', function (event) {
    var petal = event.target.closest ? event.target.closest('.motion-petal') : null;
    if (!petal) return;
    var day = days.find(function (row) { return row.date === petal.dataset.date; });
    if (!day) return;
    showTooltip(event, day, petal.dataset.sport, {
      distance_mi: petal.dataset.distance,
      elevation_ft: petal.dataset.elevation
    });
  });
  root.addEventListener('mousemove', function (event) {
    if (tooltip.hidden) return;
    tooltip.style.left = Math.max(8, Math.min(window.innerWidth - tooltip.offsetWidth - 8, event.clientX + 14)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(window.innerHeight - tooltip.offsetHeight - 8, event.clientY - tooltip.offsetHeight - 12)) + 'px';
  });
  root.addEventListener('mouseout', function (event) {
    if (event.target.classList && event.target.classList.contains('motion-petal')) scheduleHide(240);
  });
  root.addEventListener('focusin', function (event) {
    var petal = event.target.classList && event.target.classList.contains('motion-petal') ? event.target : null;
    if (!petal) return;
    var day = days.find(function (row) { return row.date === petal.dataset.date; });
    if (!day) return;
    showTooltip(event, day, petal.dataset.sport, {
      distance_mi: petal.dataset.distance,
      elevation_ft: petal.dataset.elevation
    });
  });
  root.addEventListener('focusout', function () { scheduleHide(240); });

  // Keep the panel alive while the pointer/keyboard moves onto its atlas link.
  tooltip.addEventListener('mouseenter', cancelHide);
  tooltip.addEventListener('mouseleave', function () { scheduleHide(120); });
  tooltip.addEventListener('focusin', cancelHide);
  tooltip.addEventListener('focusout', function () { scheduleHide(240); });
  function openAtlasForSport(petal) {
    if (!petal || !petal.dataset.sport) return;
    window.location.href = baseurl + '/?activity=' + encodeURIComponent(petal.dataset.sport);
  }

  root.addEventListener('click', function (event) {
    var petal = event.target.closest ? event.target.closest('.motion-petal') : null;
    openAtlasForSport(petal);
  });
  root.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var petal = event.target.classList && event.target.classList.contains('motion-petal') ? event.target : null;
    if (!petal) return;
    event.preventDefault();
    openAtlasForSport(petal);
  });

  document.querySelectorAll('.motion-filters .filter-pill').forEach(function (button) {
    button.addEventListener('click', function () {
      filter = button.dataset.sport || 'all';
      document.querySelectorAll('.motion-filters .filter-pill').forEach(function (other) {
        var active = other === button;
        other.classList.toggle('active', active);
        other.setAttribute('aria-pressed', String(active));
      });
      render();
    });
  });
  if (scaleToggle) scaleToggle.addEventListener('change', function () {
    perYearScale = scaleToggle.checked;
    render();
  });

  render();
})();
