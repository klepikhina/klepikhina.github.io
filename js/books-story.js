(function () {
  'use strict';

  var source = document.getElementById('books-data');
  if (!source) return;

  var data;
  try {
    data = JSON.parse(source.textContent);
  } catch (error) {
    return;
  }
  var allBooks = (data && data.books) || [];
  if (!allBooks.length) return;

  var LANE_COLORS = {
    'Fantasy': '#6F8C6B',
    'Literary fiction': '#7A8B6F',
    'Science fiction': '#4A90A4',
    'Mystery & thriller': '#8FA6C9',
    'Romance': '#C25450',
    'Horror': '#5E3324',
    'History': '#D4A574',
    'Memoir & biography': '#8B7355',
    'Nonfiction': '#0C589C',
    'Nature & travel': '#4A7C59',
    'Poetry': '#B08D57',
    'Young reader': '#A97448',
    'Other': '#BDB7AE',
    'Unindexed': '#D8D4CE'
  };
  var ABBREV = {
    'Literary fiction': 'Lit',
    'Science fiction': 'Sci-fi',
    'Mystery & thriller': 'Mystery',
    'Memoir & biography': 'Memoir',
    'Nature & travel': 'Nature',
    'Young reader': 'Youth'
  };
  var PALETTE = ['#6F8C6B', '#4A90A4', '#C25450', '#D4A574', '#8FA6C9', '#8B7355', '#0C589C'];

  var orbitBox = document.getElementById('books-orbit');
  var lanesBox = document.getElementById('books-lanes');
  var pagesBox = document.getElementById('books-pages-chart');
  var ageBox = document.getElementById('books-age');
  var shelfBox = document.getElementById('books-shelf');
  var scope = document.getElementById('books-scope');
  var tooltip = document.getElementById('books-tooltip');
  if (!orbitBox || !lanesBox || !pagesBox || !ageBox || !shelfBox || !tooltip) return;

  var selectedYear = 'all';
  var selectedLane = 'all';
  var hideTimer = null;

  function fmt(value) {
    return Number(value || 0).toLocaleString('en-US');
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }

  function colorFor(lane) {
    if (LANE_COLORS[lane]) return LANE_COLORS[lane];
    var hash = 0;
    String(lane).split('').forEach(function (char) { hash = (hash * 31 + char.charCodeAt(0)) % 997; });
    return PALETTE[hash % PALETTE.length];
  }

  function hashKey(key) {
    var hash = 0;
    String(key || '').split('').forEach(function (char) { hash = (hash * 31 + char.charCodeAt(0)) % 1000; });
    return hash;
  }

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

  function showTip(event, label, detail) {
    cancelHide();
    tooltip.innerHTML = '<strong>' + escapeHtml(label) + '</strong><span>' + escapeHtml(detail) + '</span>';
    tooltip.hidden = false;
    var rect = tooltip.getBoundingClientRect();
    var x = event && typeof event.clientX === 'number' ? event.clientX + 14 : 16;
    var y = event && typeof event.clientY === 'number' ? event.clientY + 14 : 16;
    if (x + rect.width > window.innerWidth - 8) x = window.innerWidth - rect.width - 8;
    if (y + rect.height > window.innerHeight - 8) y = window.innerHeight - rect.height - 8;
    tooltip.style.left = Math.max(8, x) + 'px';
    tooltip.style.top = Math.max(8, y) + 'px';
  }

  function pagesOf(book) {
    return Number(book.pages || 0);
  }

  function ageOf(book) {
    return Math.max(0, Number(book.year) - Number(book.published));
  }

  function byYear(year) {
    return allBooks.filter(function (book) {
      return year === 'all' || String(book.year) === String(year);
    });
  }

  function byLane(year) {
    return allBooks.filter(function (book) {
      return String(book.year) === String(year) &&
        (selectedLane === 'all' || book.lane === selectedLane);
    });
  }

  function visibleBooks() {
    return allBooks.filter(function (book) {
      return (selectedYear === 'all' || String(book.year) === String(selectedYear)) &&
        (selectedLane === 'all' || book.lane === selectedLane);
    });
  }

  function svgNode(name, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }

  function renderSummary() {
    var genres = Object.keys(data.lanes || {}).filter(function (lane) { return lane !== 'Unindexed'; }).length;
    var pages = allBooks.reduce(function (sum, book) { return sum + pagesOf(book); }, 0);
    var genreNode = document.getElementById('books-genres');
    var pageNode = document.getElementById('books-pages');
    if (genreNode) genreNode.textContent = String(genres);
    if (pageNode) pageNode.textContent = fmt(pages);
  }

  // Broad themes keep publishing noise (bestseller lists, series names) from
  // swamping the layout. Each subject tag can contribute to several themes.
  var THEMES = [
    { key: 'place', words: ['nature', 'travel', 'geography', 'outdoor', 'wilderness', 'forest', 'mountain', 'plants', 'botany', 'wildfire', 'fire', 'country', 'city', 'river', 'island', 'sea', 'ocean', 'desert', 'park', 'national', 'recreation', 'survival'] },
    { key: 'past', words: ['history', 'historical', 'war', 'ancient', 'medieval', 'century', 'past', 'dynasty', 'revolution'] },
    { key: 'speculative', words: ['fantasy', 'science fiction', 'sci fi', 'magic', 'magician', 'alien', 'time travel', 'supernatural', 'dragon', 'fairy', 'robot', 'future', 'imaginary', 'dystop'] },
    { key: 'life', words: ['biography', 'memoir', 'autobiography', 'life story', 'diary', 'letters', 'personal narrative'] },
    { key: 'crime', words: ['mystery', 'thriller', 'detective', 'crime', 'murder', 'suspense', 'espionage', 'spy'] },
    { key: 'ideas', words: ['politics', 'law', 'women', 'race', 'psychology', 'education', 'economics', 'religion', 'philosophy', 'social', 'society', 'health', 'medicine', 'science', 'indigenous'] },
    { key: 'relationships', words: ['love', 'romance', 'friendship', 'family', 'grief', 'mental health', 'relationships', 'marriage', 'motherhood'] }
  ];

  function normalizeSubject(subject) {
    return String(subject || '').toLowerCase()
      .replace(/\([^)]*\)/g, ' ')
      .replace(/[^a-z0-9 ]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function hasTheme(text, words) {
    var tokens = text.split(' ');
    return words.some(function (word) {
      if (word.indexOf(' ') !== -1) return text.indexOf(word) !== -1;
      return tokens.indexOf(word) !== -1;
    });
  }

  function themeVectors() {
    var documents = allBooks.filter(function (book) { return (book.subjects || []).length; });
    var vectors = {};
    Object.keys(data.lanes || {}).forEach(function (lane) {
      var books = documents.filter(function (book) { return book.lane === lane; });
      var counts = {};
      books.forEach(function (book) {
        var seen = {};
        (book.subjects || []).forEach(function (subject) {
          var text = normalizeSubject(subject);
          THEMES.forEach(function (theme) {
            if (hasTheme(text, theme.words)) seen[theme.key] = true;
          });
        });
        Object.keys(seen).forEach(function (key) { counts[key] = (counts[key] || 0) + 1; });
      });
      var vector = {};
      Object.keys(counts).forEach(function (key) { vector[key] = counts[key] / Math.max(1, books.length); });
      vectors[lane] = vector;
    });
    return vectors;
  }

  function similarityData(lanes) {
    var vectors = themeVectors();
    var matrix = {};
    lanes.forEach(function (a) {
      matrix[a] = {};
      lanes.forEach(function (b) {
        if (a === b) {
          matrix[a][b] = 1;
          return;
        }
        var dot = 0;
        var normA = 0;
        var normB = 0;
        Object.keys(vectors[a] || {}).forEach(function (key) {
          normA += vectors[a][key] * vectors[a][key];
          if (vectors[b] && vectors[b][key]) dot += vectors[a][key] * vectors[b][key];
        });
        Object.keys(vectors[b] || {}).forEach(function (key) {
          normB += vectors[b][key] * vectors[b][key];
        });
        matrix[a][b] = normA && normB ? dot / Math.sqrt(normA * normB) : 0;
      });
    });

    var raw = {};
    lanes.forEach(function (a) {
      raw[a] = {};
      lanes.forEach(function (b) { raw[a][b] = matrix[a][b]; });
    });

    var maximum = 0;
    lanes.forEach(function (a) {
      lanes.forEach(function (b) {
        if (a !== b) maximum = Math.max(maximum, matrix[a][b]);
      });
    });
    if (maximum > 0) {
      lanes.forEach(function (a) {
        lanes.forEach(function (b) {
          if (a !== b) matrix[a][b] = matrix[a][b] / maximum;
        });
      });
    }

    return { matrix: matrix, raw: raw };
  }

  function layoutOrbit(lanes, radii, matrix, width, height) {
    var cx = width / 2;
    var cy = height / 2;
    var positions = {};

    lanes.forEach(function (lane, index) {
      var angle = index * 2.3999632297;
      var distance = 70 * Math.sqrt((index + 1) / lanes.length);
      positions[lane] = {
        x: cx + distance * Math.cos(angle),
        y: cy + distance * Math.sin(angle),
        vx: 0,
        vy: 0
      };
    });

    for (var iteration = 0; iteration < 600; iteration += 1) {
      for (var i = 0; i < lanes.length; i += 1) {
        for (var j = i + 1; j < lanes.length; j += 1) {
          var a = lanes[i];
          var b = lanes[j];
          var dx = positions[a].x - positions[b].x;
          var dy = positions[a].y - positions[b].y;
          var distance = Math.sqrt(dx * dx + dy * dy) || 1;
          var minimum = radii[a] + radii[b] + 12;
          var desired = minimum + (1 - (matrix[a][b] || 0)) * 230;
          var force = (distance - desired) * 0.018;
          var ux = dx / distance;
          var uy = dy / distance;
          positions[a].vx -= ux * force;
          positions[a].vy -= uy * force;
          positions[b].vx += ux * force;
          positions[b].vy += uy * force;
        }
      }
      lanes.forEach(function (lane) {
        positions[lane].vx += (cx - positions[lane].x) * 0.0012;
        positions[lane].vy += (cy - positions[lane].y) * 0.0012;
        positions[lane].x += positions[lane].vx;
        positions[lane].y += positions[lane].vy;
        positions[lane].vx *= 0.8;
        positions[lane].vy *= 0.8;
      });
    }

    for (var settle = 0; settle < 160; settle += 1) {
      var moved = false;
      for (var m = 0; m < lanes.length; m += 1) {
        for (var n = m + 1; n < lanes.length; n += 1) {
          var left = lanes[m];
          var right = lanes[n];
          var sepX = positions[left].x - positions[right].x;
          var sepY = positions[left].y - positions[right].y;
          var sep = Math.sqrt(sepX * sepX + sepY * sepY) || 1;
          var required = radii[left] + radii[right] + 18;
          if (sep < required) {
            var push = (required - sep) / 2 + 0.1;
            positions[left].x += (sepX / sep) * push;
            positions[left].y += (sepY / sep) * push;
            positions[right].x -= (sepX / sep) * push;
            positions[right].y -= (sepY / sep) * push;
            moved = true;
          }
        }
      }
      if (!moved) break;
    }

    var minX = Infinity;
    var maxX = -Infinity;
    var minY = Infinity;
    var maxY = -Infinity;
    lanes.forEach(function (lane) {
      var position = positions[lane];
      var radius = radii[lane];
      minX = Math.min(minX, position.x - radius);
      maxX = Math.max(maxX, position.x + radius);
      minY = Math.min(minY, position.y - radius);
      maxY = Math.max(maxY, position.y + radius);
    });
    var pad = 18;
    var scale = Math.min(
      (width - 2 * pad) / (maxX - minX),
      (height - 2 * pad) / (maxY - minY),
      1.5
    );
    var middleX = (minX + maxX) / 2;
    var middleY = (minY + maxY) / 2;
    lanes.forEach(function (lane) {
      positions[lane].x = cx + (positions[lane].x - middleX) * scale;
      positions[lane].y = cy + (positions[lane].y - middleY) * scale;
    });
    return { positions: positions, scale: scale };
  }

  function renderOrbit() {
    orbitBox.textContent = '';
    var counts = {};
    byYear(selectedYear).forEach(function (book) {
      var row = counts[book.lane] || { books: 0, pages: 0 };
      row.books += 1;
      row.pages += pagesOf(book);
      counts[book.lane] = row;
    });
    var lanes = Object.keys(counts).filter(function (lane) { return counts[lane].books; })
      .sort(function (a, b) { return counts[b].books - counts[a].books; });
    if (!lanes.length) return;

    var width = orbitBox.clientWidth || 640;
    var height = 340;
    var radii = {};
    lanes.forEach(function (lane) { radii[lane] = 18 + Math.sqrt(counts[lane].books) * 6; });
    var similarity = similarityData(lanes);
    var layout = layoutOrbit(lanes, radii, similarity.matrix, width, height);
    var positions = layout.positions;
    if (layout.scale < 1) {
      lanes.forEach(function (lane) { radii[lane] = Math.max(9, radii[lane] * layout.scale); });
    }

    function nearest(lane) {
      var best = null;
      var bestDistance = Infinity;
      lanes.forEach(function (other) {
        if (other === lane) return;
        var dx = positions[lane].x - positions[other].x;
        var dy = positions[lane].y - positions[other].y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = other;
        }
      });
      return best ? { lane: best, score: similarity.raw[lane][best] || 0 } : null;
    }

    var svg = svgNode('svg', {
      viewBox: '0 0 ' + width + ' ' + height,
      role: 'img',
      'aria-label': 'Genres placed by theme similarity; nearby genres share more themes'
    });

    lanes.forEach(function (lane) {
      var position = positions[lane];
      var radius = radii[lane];
      var neighbor = nearest(lane);
      var detail = counts[lane].books + ' books · ' + fmt(counts[lane].pages) + ' pages' +
        (neighbor ? ' · nearest: ' + neighbor.lane + ' · theme similarity ' + neighbor.score.toFixed(2) : '');
      var group = svgNode('g', { class: 'books-orbit__group' + (selectedLane === lane ? ' is-active' : '') });
      group.dataset.label = lane;
      group.dataset.detail = detail;
      group.dataset.lane = lane;
      var bubble = svgNode('circle', {
        cx: position.x.toFixed(1), cy: position.y.toFixed(1), r: radius.toFixed(1),
        fill: colorFor(lane), 'fill-opacity': selectedLane === 'all' || selectedLane === lane ? '0.88' : '0.3',
        stroke: selectedLane === lane ? '#1F3326' : '#fff', 'stroke-width': selectedLane === lane ? 3 : 1.5,
        tabindex: 0, role: 'button', class: 'books-orbit__bubble',
        'aria-label': lane + ': ' + counts[lane].books + ' books'
      });
      bubble.dataset.label = lane;
      bubble.dataset.detail = detail;
      bubble.dataset.lane = lane;

      var label = svgNode('text', {
        x: position.x.toFixed(1), y: (position.y + 4).toFixed(1), 'text-anchor': 'middle',
        fill: '#fff', stroke: 'rgba(0,0,0,0.22)', 'stroke-width': 3,
        'paint-order': 'stroke', 'font-size': radius > 34 ? 12 : 10, 'font-weight': 600
      });
      label.textContent = ABBREV[lane] || lane;

      group.appendChild(bubble);
      group.appendChild(label);
      svg.appendChild(group);
    });

    orbitBox.appendChild(svg);
  }

  function renderLanes() {
    lanesBox.textContent = '';
    var ordered = Object.keys(data.lanes || {}).sort(function (a, b) { return data.lanes[b] - data.lanes[a]; });
    var counts = byYear(selectedYear).reduce(function (totals, book) {
      totals[book.lane] = (totals[book.lane] || 0) + 1;
      return totals;
    }, {});

    [['all', 'All genres']].concat(ordered.map(function (lane) { return [lane, lane]; })).forEach(function (pair) {
      var lane = pair[0];
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'books-lane' + (selectedLane === lane ? ' active' : '');
      button.setAttribute('aria-pressed', selectedLane === lane ? 'true' : 'false');
      button.dataset.lane = lane;
      var dot = document.createElement('i');
      dot.className = 'books-lane__dot';
      dot.style.backgroundColor = lane === 'all' ? '#1F3326' : colorFor(lane);
      button.appendChild(dot);
      button.appendChild(document.createTextNode(pair[1] + (lane === 'all' ? '' : ' · ' + (counts[lane] || 0))));
      button.dataset.label = pair[1];
      button.dataset.detail = lane === 'all'
        ? 'Showing every genre'
        : (counts[lane] || 0) + ' books in this selection';
      lanesBox.appendChild(button);
    });
  }

  function renderPages() {
    pagesBox.textContent = '';
    var rows = (data.years || []).map(function (row) {
      var books = byLane(row.year);
      var total = books.reduce(function (sum, book) { return sum + pagesOf(book); }, 0);
      var lanes = {};
      books.forEach(function (book) {
        lanes[book.lane] = (lanes[book.lane] || 0) + pagesOf(book);
      });
      return { year: row.year, total: total, lanes: lanes };
    });
    var max = rows.reduce(function (value, row) { return Math.max(value, row.total); }, 1);

    rows.forEach(function (row) {
      var line = document.createElement('div');
      line.className = 'books-pages__row' + (String(row.year) === String(selectedYear) ? ' is-active' : '');

      var yearButton = document.createElement('button');
      yearButton.type = 'button';
      yearButton.className = 'books-pages__year';
      yearButton.textContent = String(row.year);
      yearButton.dataset.year = String(row.year);
      yearButton.setAttribute('aria-label', 'Focus ' + row.year + ', ' + fmt(row.total) + ' pages');
      line.appendChild(yearButton);

      var track = document.createElement('span');
      track.className = 'books-pages__track';
      var fill = document.createElement('span');
      fill.className = 'books-pages__fill';
      fill.style.width = ((row.total / max) * 100).toFixed(2) + '%';
      Object.keys(row.lanes).sort(function (a, b) { return row.lanes[b] - row.lanes[a]; }).forEach(function (lane) {
        var segment = document.createElement('button');
        segment.type = 'button';
        segment.className = 'books-pages__segment';
        segment.style.width = row.total ? ((row.lanes[lane] / row.total) * 100).toFixed(2) + '%' : '0%';
        segment.style.backgroundColor = colorFor(lane);
        segment.dataset.label = row.year + ' · ' + lane;
        segment.dataset.detail = fmt(row.lanes[lane]) + ' of ' + fmt(row.total) + ' pages';
        segment.dataset.lane = lane;
        segment.setAttribute('aria-label', row.year + ' ' + lane + ': ' + fmt(row.lanes[lane]) + ' pages');
        fill.appendChild(segment);
      });
      track.appendChild(fill);
      line.appendChild(track);

      var count = document.createElement('span');
      count.className = 'books-pages__count';
      count.textContent = fmt(row.total);
      line.appendChild(count);
      pagesBox.appendChild(line);
    });
  }

  function renderAge() {
    ageBox.textContent = '';
    var books = visibleBooks().filter(function (book) { return book.published; });
    var yearsShown = selectedYear === 'all'
      ? (data.years || []).map(function (row) { return String(row.year); })
      : [String(selectedYear)];
    var width = ageBox.clientWidth || 640;
    var height = 270;
    var left = 42;
    var right = 14;
    var top = 22;
    var bottom = 36;
    var chartWidth = width - left - right;
    var chartHeight = height - top - bottom;

    var minAge = 0;
    var maxAge = 100;
    var ticks = [0, 25, 50, 75, 100];
    if (selectedYear !== 'all' && books.length) {
      var ages = books.map(ageOf);
      var low = Math.min.apply(null, ages);
      var high = Math.max.apply(null, ages);
      var span = Math.max(10, high - low);
      minAge = Math.max(0, Math.floor((low - span * 0.12) / 5) * 5);
      maxAge = Math.ceil((high + span * 0.12) / 5) * 5;
      if (maxAge <= minAge) maxAge = minAge + 10;
      ticks = [];
      for (var tick = minAge; tick <= maxAge; tick += (maxAge - minAge) / 4) {
        ticks.push(Math.round(tick));
      }
    }

    var svg = svgNode('svg', {
      viewBox: '0 0 ' + width + ' ' + height,
      role: 'img',
      'aria-label': 'Years between publication and reading, zoomed to the selected year'
    });

    var axisLabel = svgNode('text', {
      x: left, y: top - 8, fill: '#666', 'font-size': 10
    });
    axisLabel.textContent = 'years old';
    svg.appendChild(axisLabel);

    ticks.forEach(function (age) {
      var y = top + chartHeight * (1 - (age - minAge) / (maxAge - minAge));
      svg.appendChild(svgNode('line', {
        x1: left, y1: y, x2: width - right, y2: y,
        stroke: '#E8E4DF', 'stroke-width': 1
      }));
      var label = svgNode('text', {
        x: left - 8, y: y + 4, 'text-anchor': 'end', fill: '#666', 'font-size': 11
      });
      label.textContent = String(age);
      svg.appendChild(label);
    });

    yearsShown.forEach(function (year, index) {
      var x = left + (yearsShown.length === 1 ? chartWidth / 2 : (index / (yearsShown.length - 1)) * chartWidth);
      var label = svgNode('text', {
        x: x, y: height - 10, 'text-anchor': 'middle', fill: '#666', 'font-size': 11
      });
      label.textContent = year;
      svg.appendChild(label);
    });

    books.forEach(function (book) {
      var index = yearsShown.indexOf(String(book.year));
      if (index < 0) return;
      var jitter = ((hashKey(book.key || book.title) % 100) / 100 - 0.5);
      var x = left + (yearsShown.length === 1
        ? chartWidth / 2 + jitter * chartWidth * 0.6
        : (index / (yearsShown.length - 1)) * chartWidth + jitter * 28);
      var age = Math.max(minAge, Math.min(maxAge, ageOf(book)));
      var y = top + chartHeight * (1 - (age - minAge) / (maxAge - minAge));
      var radius = 3 + Math.min(4, Math.sqrt(pagesOf(book)) / 9);
      var dot = svgNode('circle', {
        cx: x.toFixed(1), cy: y.toFixed(1), r: radius.toFixed(1),
        fill: colorFor(book.lane), 'fill-opacity': '0.78', stroke: '#fff', 'stroke-width': 1,
        tabindex: 0, role: 'button', class: 'books-age__dot',
        'aria-label': book.title + ', published ' + book.published
      });
      dot.dataset.label = book.title;
      dot.dataset.detail = (book.author || 'Unknown author') + ' · ' + book.lane + ' · ' +
        fmt(book.pages) + ' pages · published ' + book.published + ' · ' + ageOf(book) + ' years old';
      dot.dataset.lane = book.lane;
      svg.appendChild(dot);
    });

    ageBox.appendChild(svg);
  }

  function compactPages(value) {
    var pages = Number(value || 0);
    if (pages < 1000) return String(pages);
    return (pages / 1000).toFixed(pages >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k';
  }

  function renderShelf() {
    shelfBox.textContent = '';
    var formatOrder = { audio: 0, physical: 1 };

    (data.years || []).forEach(function (row, yearIndex) {
      if (selectedYear !== 'all' && String(row.year) !== String(selectedYear)) return;
      var books = byLane(row.year).slice().sort(function (a, b) {
        var left = formatOrder[a.format] === undefined ? 9 : formatOrder[a.format];
        var right = formatOrder[b.format] === undefined ? 9 : formatOrder[b.format];
        return left - right;
      });
      if (!books.length) return;

      var shelf = document.createElement('div');
      shelf.className = 'books-shelf__shelf ' + (yearIndex % 2 ? 'is-right' : 'is-left');
      shelf.style.setProperty('--shelf-count', String(books.length));

      var bar = document.createElement('div');
      bar.className = 'books-shelf__yearbar';
      var bracket = document.createElement('span');
      bracket.className = 'books-shelf__bracket';
      bracket.style.gridColumn = 'span ' + books.length;
      var total = books.reduce(function (sum, book) { return sum + pagesOf(book); }, 0);
      var label = document.createElement('span');
      label.className = 'books-shelf__label';
      var yearLabel = document.createElement('span');
      yearLabel.className = 'books-shelf__label-year';
      yearLabel.textContent = String(row.year);
      var pageLabel = document.createElement('span');
      pageLabel.className = 'books-shelf__label-pages';
      pageLabel.textContent = compactPages(total) + ' p';
      label.appendChild(yearLabel);
      label.appendChild(pageLabel);
      bracket.appendChild(label);
      bar.appendChild(bracket);

      var spines = document.createElement('div');
      spines.className = 'books-shelf__books';
      books.forEach(function (book) {
        var height = Math.round(28 + Math.min(70, (pagesOf(book) / 700) * 70));
        var spine = document.createElement('button');
        spine.type = 'button';
        spine.className = 'books-spine';
        spine.style.height = height + 'px';
        spine.style.backgroundColor = colorFor(book.lane);
        spine.dataset.label = book.title;
        spine.dataset.detail = (book.author || 'Unknown author') + ' · ' + book.lane + ' · ' +
          fmt(book.pages) + ' pages · ' + (book.format || 'book');
        spine.dataset.lane = book.lane;
        spine.dataset.year = String(book.year);
        spine.dataset.format = book.format || '';
        spine.setAttribute('aria-label', book.title + ': ' + fmt(book.pages) + ' pages, ' + book.lane);
        spines.appendChild(spine);
      });

      var board = document.createElement('div');
      board.className = 'books-shelf__board';

      shelf.appendChild(bar);
      shelf.appendChild(spines);
      shelf.appendChild(board);
      shelfBox.appendChild(shelf);
    });
  }

  function draw() {
    var books = visibleBooks();
    var pages = books.reduce(function (sum, book) { return sum + pagesOf(book); }, 0);
    var label = selectedYear === 'all' ? 'All years' : String(selectedYear);
    if (selectedLane !== 'all') label += ' · ' + selectedLane;
    if (scope) scope.textContent = label + ' · ' + books.length + ' books · ' + fmt(pages) + ' pages';
    renderOrbit();
    renderLanes();
    renderPages();
    renderAge();
    renderShelf();
  }

  function selectYear(value) {
    selectedYear = value;
    document.querySelectorAll('.books-filters [data-year]').forEach(function (button) {
      var active = button.dataset.year === value;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    draw();
  }

  function selectLane(value) {
    selectedLane = value;
    draw();
  }

  function wire(container) {
    container.addEventListener('mouseover', function (event) {
      var target = event.target.closest ? event.target.closest('[data-label]') : null;
      if (!target) return;
      showTip(event, target.dataset.label, target.dataset.detail || '');
    });
    container.addEventListener('mouseout', function (event) {
      if (event.target.dataset && event.target.dataset.label) scheduleHide(220);
    });
    container.addEventListener('focusin', function (event) {
      if (!event.target.dataset || !event.target.dataset.label) return;
      var rect = event.target.getBoundingClientRect();
      showTip({ clientX: rect.left, clientY: rect.bottom }, event.target.dataset.label, event.target.dataset.detail || '');
    });
    container.addEventListener('focusout', function () { scheduleHide(220); });
  }

  tooltip.addEventListener('mouseenter', cancelHide);
  tooltip.addEventListener('mouseleave', function () { scheduleHide(120); });

  document.querySelectorAll('.books-filters [data-year]').forEach(function (button) {
    button.addEventListener('click', function () { selectYear(button.dataset.year); });
  });
  [orbitBox, lanesBox, pagesBox, ageBox, shelfBox].forEach(wire);

  orbitBox.addEventListener('click', function (event) {
    var bubble = event.target.closest ? event.target.closest('[data-lane]') : null;
    if (bubble) selectLane(selectedLane === bubble.dataset.lane ? 'all' : bubble.dataset.lane);
  });
  lanesBox.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-lane]') : null;
    if (button) selectLane(button.dataset.lane);
  });
  pagesBox.addEventListener('click', function (event) {
    var target = event.target.closest ? event.target.closest('[data-lane],[data-year]') : null;
    if (!target) return;
    if (target.dataset.lane) selectLane(selectedLane === target.dataset.lane ? 'all' : target.dataset.lane);
    else selectYear(target.dataset.year);
  });
  ageBox.addEventListener('click', function (event) {
    var dot = event.target.closest ? event.target.closest('[data-lane]') : null;
    if (dot) selectLane(selectedLane === dot.dataset.lane ? 'all' : dot.dataset.lane);
  });
  shelfBox.addEventListener('click', function (event) {
    var spine = event.target.closest ? event.target.closest('[data-lane]') : null;
    if (spine) selectLane(selectedLane === spine.dataset.lane ? 'all' : spine.dataset.lane);
  });

  var resizeTimer = null;
  window.addEventListener('resize', function () {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(draw, 150);
  });

  renderSummary();
  draw();
})();
