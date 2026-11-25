/*
 * Adventure Atlas
 * ---------------------------------------------------------------------------
 * MapLibre layers:
 *   1. data/activity_density.geojson - anonymised coarse-cell heatmap
 *   2. data/regions.geojson          - Natural Earth states/provinces/countries
 *   3. data/featured_routes.geojson  - curated, blog-linked routes and crags
 *
 * Public surface: window.AtlasMap.init(containerId, options)
 */
(function () {
  'use strict';

  var ACTIVITY_COLORS = {
    bike: '#C25450',
    climb: '#D4A574',
    hike: '#4A7C59',
    run: '#4A90A4',
    ski: '#8FA6C9',
    water: '#0C589C'
  };

  // Shared with the legend so the density key always shows the heatmap ramp.
  var HEATMAP_COLORS = [
    'rgba(255, 236, 179, 0)',
    'rgba(250, 220, 130, 0.55)',
    '#F6C96B',
    '#EE9A4C',
    '#D9643F',
    '#93352A'
  ];

  var ACTIVITY_LABELS = {
    bike: 'Bike',
    climb: 'Climb',
    hike: 'Hike',
    run: 'Run',
    ski: 'Ski',
    water: 'Water'
  };

  var METRICS = {
    activity: {
      property: function (filter) {
        return filter === 'all' ? 'activity_count' : filter + '_trips';
      },
      short: 'Activities',
      label: 'Activities logged',
      unit: 'activities',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    },
    vert: {
      property: function (filter) {
        return (filter === 'ski' ? 'ski' : filter) + '_vert_ft';
      },
      short: 'Vert',
      label: 'Logged vert',
      unit: 'ft',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    },
    distance: {
      property: function (filter) { return filter + '_distance_mi'; },
      short: 'Distance',
      label: 'Distance logged',
      unit: 'mi',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    },
    touring: {
      property: 'ski_touring_count',
      short: 'Touring',
      label: 'Backcountry touring',
      unit: '%',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    },
    alpine: {
      property: 'ski_alpine_count',
      short: 'Alpine',
      label: 'Alpine / resort',
      unit: '%',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    },
    nordic: {
      property: 'ski_nordic_count',
      short: 'Nordic',
      label: 'Nordic skiing',
      unit: '%',
      colors: ['#6F8C6B', '#527751', '#3A5E3D', '#28482E', '#173420']
    }
  };

  // Which shading metrics make sense for each activity type.
  var FILTER_METRICS = {
    all: ['activity'],
    bike: ['activity', 'vert', 'distance'],
    hike: ['activity', 'vert', 'distance'],
    run: ['activity', 'vert', 'distance'],
    climb: ['activity'],
    ski: ['activity', 'vert', 'touring', 'alpine', 'nordic'],
    water: ['activity']
  };

  var state = {
    map: null,
    container: null,
    regions: null,
    routes: null,
    density: null,
    filter: 'all',
    metric: 'activity',
    terrain: false,
    hoveredRegion: null,
    hoveredRoute: null,
    tooltip: null,
    drawer: null,
    backdrop: null,
    lastFocus: null
  };

  // -------------------------------------------------------------------------
  // Utilities
  // -------------------------------------------------------------------------

  function formatNumber(value) {
    if (value === null || value === undefined || value === '') return '\u2014';
    var number = Number(value);
    if (isNaN(number)) return String(value);
    return number.toLocaleString('en-US', { maximumFractionDigits: 1 });
  }

  // Stats read "1 Activity" / "5 Activities" instead of always using the plural.
  function activityNoun(count, capitalize) {
    var word = Number(count) === 1 ? 'activity' : 'activities';
    return capitalize ? word.charAt(0).toUpperCase() + word.slice(1) : word;
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // MapLibre serialises nested GeoJSON properties (arrays, objects) to JSON
  // strings on rendered features. Decode either shape.
  function asArray(value) {
    if (Array.isArray(value)) return value;
    if (typeof value === 'string' && value.trim()) {
      try {
        var parsed = JSON.parse(value);
        if (Array.isArray(parsed)) return parsed;
      } catch (error) {
        /* fall through to comma splitting */
      }
      return value.split(',').map(function (item) { return item.trim(); }).filter(Boolean);
    }
    return [];
  }

  function activityDot(activity) {
    var color = ACTIVITY_COLORS[activity] || '#6B6B6B';
    var label = ACTIVITY_LABELS[activity] || activity;
    return '<span class="atlas-sport" style="--sport-color:' + color + '" title="' + escapeHtml(label) + '" aria-label="' + escapeHtml(label) + '"></span>';
  }

  function splitTitle(title) {
    var text = String(title || '');
    var parts = text.split(' | ');
    return { main: parts[0], detail: parts.slice(1).join(' \u00b7 ') };
  }

  function fetchJson(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status + ' for ' + url);
      return response.json();
    });
  }

  // Allow embedded data (used by the standalone preview) as well as URLs.
  function jsonSource(data, url) {
    return data !== undefined && data !== null ? Promise.resolve(data) : fetchJson(url);
  }

  function metricProperty(metric) {
    var property = METRICS[metric].property;
    return typeof property === 'function' ? property(state.filter) : property;
  }

  function metricValues(metric) {
    var property = metricProperty(metric);
    return (state.regions.features || []).map(function (feature) {
      return Number(feature.properties[property]) || 0;
    });
  }

  function metricValueExpression() {
    return ['coalesce', ['get', metricProperty(state.metric)], 0];
  }

  // Use percentiles instead of min/max: one hometown with thousands of activities
  // otherwise flattens every other region into the palest shade.
  function metricExpression(metric) {
    var config = METRICS[metric];
    var positive = metricValues(metric).filter(function (value) { return value > 0; })
      .sort(function (a, b) { return a - b; });
    var stops = [0];
    var last = config.colors.length - 1;
    if (positive.length === 1) {
      for (var single = 1; single <= last; single += 1) {
        stops.push((positive[0] * single) / last);
      }
    } else if (positive.length > 1) {
      for (var index = 1; index <= last; index += 1) {
        var position = Math.round((index - 1) * (positive.length - 1) / (last - 1));
        var stop = positive[position];
        if (stop <= stops[stops.length - 1]) {
          stop = stops[stops.length - 1] + Math.max(1, stop * 0.01);
        }
        stops.push(stop);
      }
      var max = positive[positive.length - 1];
      if (stops[last] > max) {
        stops[last] = max;
        if (stops[last - 1] >= max) {
          stops[last - 1] = max - Math.max(0.5, max * 0.01);
        }
      }
    } else {
      for (var empty = 1; empty <= last; empty += 1) stops.push(empty);
    }

    var expression = ['interpolate', ['linear'], metricValueExpression()];
    stops.forEach(function (stop, stopIndex) {
      expression.push(stop, config.colors[stopIndex]);
    });
    return expression;
  }

  // A region with no activity of the selected type drops back to the basemap,
  // so the green footprint changes as clearly as the heatmap does. Regions with
  // only one or two activities stay visible instead of nearly disappearing.
  function regionFillOpacity() {
    return [
      'case',
      ['boolean', ['feature-state', 'hover'], false], 0.68,
      ['>', metricValueExpression(), 0], 0.52,
      0.06
    ];
  }

  function densityProperty() {
    if (state.metric === 'touring' || state.metric === 'alpine' || state.metric === 'nordic') {
      return 'ski_' + state.metric + '_count';
    }
    var sport = state.filter === 'all' ? '' : state.filter + '_';
    if (state.metric === 'vert') return (state.filter === 'ski' ? 'ski_' : sport) + 'vert_ft';
    if (state.metric === 'distance') return sport + 'distance_mi';
    return state.filter === 'all' ? 'count' : state.filter + '_count';
  }

  function densityLabel() {
    var property = densityProperty();
    if (property === 'count') return 'Activity density';
    if (property === 'ski_count') return 'Ski activity density';
    if (property === 'vert_ft' || property === 'distance_mi') {
      return property === 'vert_ft' ? 'Vert density' : 'Distance density';
    }
    var measure = property.match(/^(.+?)_(vert_ft|distance_mi)$/);
    if (measure) {
      var activityName = measure[1].charAt(0).toUpperCase() + measure[1].slice(1);
      return activityName + (measure[2] === 'vert_ft' ? ' vert density' : ' distance density');
    }
    if (property.indexOf('ski_') === 0) {
      var style = property.slice(4).replace(/_count$/, '');
      return style.charAt(0).toUpperCase() + style.slice(1) + ' skiing density';
    }
    var sport = property.replace(/_count$/, '');
    return sport.charAt(0).toUpperCase() + sport.slice(1) + ' activity density';
  }

  // The heatmap is drawn from anonymised grid-cell counts, not private points.
  // Stops come from the data's quantiles, so a one- or two-activity cell is
  // still visible instead of being flattened by one very dense hometown.
  function densityWeightExpression() {
    var property = densityProperty();
    var values = (state.density && state.density.features || []).map(function (feature) {
      return Number(feature.properties[property]) || 0;
    });
    var positive = values.filter(function (value) { return value > 0; })
      .sort(function (a, b) { return a - b; });
    if (!positive.length) {
      return ['interpolate', ['linear'], ['coalesce', ['get', property], 0], 0, 0, 1, 1];
    }

    var pick = function (fraction) {
      return positive[Math.round(fraction * (positive.length - 1))];
    };
    var low = Math.max(1, positive[0]);
    var mid = Math.max(low + 1, pick(0.5));
    var high = Math.max(mid + 1, pick(0.9));
    var top = Math.max(high + 1, positive[positive.length - 1]);

    function scale(stops) {
      var expression = ['interpolate', ['linear'], ['coalesce', ['get', property], 0]];
      stops.forEach(function (pair) { expression.push(pair[0], pair[1]); });
      return expression;
    }

    // Zooming in swaps to the darker scale: within a region such as Kentucky,
    // its own busy cells become the red end of the gradient even when their
    // counts are tiny compared with the global maximum.
    var wide = scale([
      [0, 0], [low, 0.34], [mid, 0.6], [high, 0.85], [top, 1]
    ]);
    var close = scale([
      [0, 0], [low, 0.55], [mid, 0.85], [high, 1], [top, 1]
    ]);
    return [
      'interpolate', ['linear'], ['zoom'],
      0, wide,
      4, wide,
      8, close,
      12, close
    ];
  }

  function heatmapColorExpression() {
    return [
      'interpolate', ['linear'], ['heatmap-density'],
      0, HEATMAP_COLORS[0],
      0.16, HEATMAP_COLORS[1],
      0.35, HEATMAP_COLORS[2],
      0.58, HEATMAP_COLORS[3],
      0.78, HEATMAP_COLORS[4],
      1, HEATMAP_COLORS[5]
    ];
  }

  function routeRadius(base) {
    return [
      'interpolate', ['linear'], ['zoom'],
      3, ['case', ['boolean', ['feature-state', 'hover'], false], base + 2.6, base],
      8, ['case', ['boolean', ['feature-state', 'hover'], false], base + 3.4, base + 2.0],
      13, ['case', ['boolean', ['feature-state', 'hover'], false], base + 4.4, base + 3.4]
    ];
  }

  function featureBounds(features) {
    var bounds = new maplibregl.LngLatBounds();
    function walk(coords) {
      if (!coords) return;
      if (typeof coords[0] === 'number' && typeof coords[1] === 'number') {
        bounds.extend([coords[0], coords[1]]);
        return;
      }
      coords.forEach(walk);
    }
    (features || []).forEach(function (feature) {
      if (feature && feature.geometry) walk(feature.geometry.coordinates);
    });
    return bounds;
  }

  // -------------------------------------------------------------------------
  // Map style
  // -------------------------------------------------------------------------

  function buildStyle() {
    return {
      version: 8,
      glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
      sources: {
        'basemap': {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          maxzoom: 19,
          attribution: '&copy; OpenStreetMap contributors'
        },
        'terrain-dem': {
          type: 'raster-dem',
          tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
          encoding: 'terrarium',
          tileSize: 256,
          maxzoom: 14,
          attribution: 'Terrain: Mapzen / AWS Open Data'
        }
      },
      layers: [
        {
          id: 'background',
          type: 'background',
          paint: { 'background-color': '#F6F5F1' }
        },
        {
          id: 'basemap',
          type: 'raster',
          source: 'basemap',
          paint: {
            'raster-opacity': 0.9,
            'raster-saturation': -0.45,
            'raster-contrast': 0.02,
            'raster-brightness-max': 0.96
          }
        },
        {
          id: 'hillshade',
          type: 'hillshade',
          source: 'terrain-dem',
          layout: { visibility: 'none' },
          paint: {
            'hillshade-exaggeration': 0.38,
            'hillshade-shadow-color': '#6B7A63',
            'hillshade-highlight-color': '#FFFFFF',
            'hillshade-accent-color': '#8A9A84'
          }
        }
      ]
    };
  }

  // -------------------------------------------------------------------------
  // Layers
  // -------------------------------------------------------------------------

  function buildLabelFeatures() {
    return (state.regions.features || []).map(function (feature) {
      var centroid = asArray(feature.properties.centroid);
      if (!centroid.length) return null;
      return {
        type: 'Feature',
        properties: {
          region_name: feature.properties.region_name,
          region_kind: feature.properties.region_kind
        },
        geometry: { type: 'Point', coordinates: centroid }
      };
    }).filter(Boolean);
  }

  function addAtlasLayers() {
    var map = state.map;

    // The basemap comes from the free, keyless OpenFreeMap Positron style.
    // Add the terrain source and hillshade on top of it before the atlas layers.
    map.addSource('terrain-dem', {
      type: 'raster-dem',
      tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
      encoding: 'terrarium',
      tileSize: 256,
      maxzoom: 14,
      attribution: 'Terrain: Mapzen / AWS Open Data'
    });
    map.addLayer({
      id: 'hillshade',
      type: 'hillshade',
      source: 'terrain-dem',
      layout: { visibility: 'none' },
      paint: {
        'hillshade-exaggeration': 0.38,
        'hillshade-shadow-color': '#6B7A63',
        'hillshade-highlight-color': '#FFFFFF',
        'hillshade-accent-color': '#8A9A84'
      }
    });

    map.addSource('regions', { type: 'geojson', data: state.regions, generateId: true });
    map.addSource('routes', { type: 'geojson', data: state.routes, generateId: true });

    // Green admin regions are quiet context; the heatmap is drawn above them
    // and the route markers above that.
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': metricExpression(state.metric),
        'fill-opacity': regionFillOpacity()
      }
    });

    if (state.density) {
      map.addSource('density', { type: 'geojson', data: state.density });
      map.addLayer({
        id: 'activity-heat',
        type: 'heatmap',
        source: 'density',
        maxzoom: 12,
        paint: {
          'heatmap-weight': densityWeightExpression(),
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1.1, 4, 1.5, 8, 2.0, 12, 2.3],
          'heatmap-color': heatmapColorExpression(),
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 24, 4, 38, 8, 64, 12, 92],
          'heatmap-opacity': 0.9
        }
      });
    }

    map.addLayer({
      id: 'regions-line',
      type: 'line',
      source: 'regions',
      paint: {
        'line-color': ['case', ['boolean', ['feature-state', 'hover'], false], '#2D5A3D', 'rgba(61, 61, 61, 0.65)'],
        'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 2.0, 1.0],
        'line-opacity': 0.8
      }
    });

    map.addSource('region-labels', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: buildLabelFeatures() }
    });
    map.addLayer({
      id: 'region-labels',
      type: 'symbol',
      source: 'region-labels',
      minzoom: 1,
      layout: {
        'text-field': ['get', 'region_name'],
        'text-font': ['Noto Sans Bold'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 2, 10, 5, 12, 9, 14],
        'text-max-width': 8,
        'text-allow-overlap': false,
        'text-padding': 6
      },
      paint: {
        'text-color': '#2D5A3D',
        'text-halo-color': 'rgba(255, 255, 255, 0.92)',
        'text-halo-width': 1.4
      }
    });

    map.addLayer({
      id: 'routes-halo',
      type: 'circle',
      source: 'routes',
      paint: {
        'circle-radius': routeRadius(6.0),
        'circle-color': '#FAFAF8',
        'circle-opacity': 0.95,
        'circle-stroke-color': 'rgba(61, 61, 61, 0.12)',
        'circle-stroke-width': 0.6
      }
    });

    map.addLayer({
      id: 'routes-dot',
      type: 'circle',
      source: 'routes',
      paint: {
        'circle-radius': routeRadius(4.2),
        'circle-color': [
          'match', ['get', 'activity'],
          'bike', ACTIVITY_COLORS.bike,
          'climb', ACTIVITY_COLORS.climb,
          'hike', ACTIVITY_COLORS.hike,
          'run', ACTIVITY_COLORS.run,
          'ski', ACTIVITY_COLORS.ski,
          'water', ACTIVITY_COLORS.water,
          '#6B6B6B'
        ],
        'circle-stroke-color': '#FFFFFF',
        'circle-stroke-width': 1.6,
        'circle-opacity': 0.98
      }
    });

    map.addLayer({
      id: 'routes-hit',
      type: 'circle',
      source: 'routes',
      paint: {
        'circle-radius': 17,
        'circle-color': '#000000',
        'circle-opacity': 0
      }
    });
  }

  // -------------------------------------------------------------------------
  // Tooltip
  // -------------------------------------------------------------------------

  function showTooltip(point, html) {
    var tooltip = state.tooltip;
    if (!tooltip) return;
    tooltip.innerHTML = html;
    tooltip.classList.add('is-visible');

    var padding = 14;
    var width = tooltip.offsetWidth;
    var height = tooltip.offsetHeight;
    var maxX = state.container.clientWidth - width - padding;
    var maxY = state.container.clientHeight - height - padding;
    var left = point.x + 16;
    var top = point.y + 16;
    if (left > maxX) left = point.x - width - 16;
    if (top > maxY) top = point.y - height - 16;
    left = Math.max(padding, Math.min(left, maxX));
    top = Math.max(padding, Math.min(top, maxY));
    tooltip.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(top) + 'px)';
  }

  function hideTooltip() {
    if (state.tooltip) state.tooltip.classList.remove('is-visible');
  }

  function stat(label, value) {
    return '<span><strong>' + escapeHtml(value) + '</strong> ' + escapeHtml(label) + '</span>';
  }

  function regionStats(properties) {
    var filter = state.filter;
    var sport = (ACTIVITY_LABELS[filter] || '').toLowerCase();
    var prefix = filter + '_';
    if (filter === 'all') {
      return [
        { label: activityNoun(properties.activity_count, true), value: formatNumber(properties.activity_count) },
        { label: 'Stories', value: formatNumber(properties.featured_story_count) }
      ];
    }
    if (filter === 'ski') {
      return [
        { label: activityNoun(properties.ski_trips, true), value: formatNumber(properties.ski_trips) },
        { label: 'Vert skied', value: formatNumber(properties.ski_vert_ft) + ' ft' },
        { label: 'Touring', value: formatNumber(properties.ski_touring_pct) + '%' },
        { label: 'Alpine', value: formatNumber(properties.ski_alpine_pct) + '%' },
        { label: 'Nordic', value: formatNumber(properties.ski_nordic_pct) + '%' }
      ];
    }
    var count = properties[prefix + 'trips'];
    if (filter === 'climb' || filter === 'water') {
      return [{ label: sport + ' ' + activityNoun(count), value: formatNumber(count) }];
    }
    return [
      { label: sport + ' ' + activityNoun(count), value: formatNumber(count) },
      { label: 'Vert', value: formatNumber(properties[prefix + 'vert_ft']) + ' ft' },
      { label: 'Distance', value: formatNumber(properties[prefix + 'distance_mi']) + ' mi' }
    ];
  }

  function statsHtml(stats) {
    return stats.map(function (item) { return stat(item.label, item.value); }).join('');
  }

  function regionTooltip(properties) {
    var sports = asArray(properties.sports).map(activityDot).join('');
    return (
      '<div class="atlas-tooltip__title">' + escapeHtml(properties.region_name) + '</div>' +
      '<div class="atlas-tooltip__stats">' + statsHtml(regionStats(properties)) + '</div>' +
      '<div class="atlas-tooltip__sports">' + sports + '</div>' +
      '<div class="atlas-tooltip__hint">Click to zoom in &amp; see stats</div>'
    );
  }

  function routeTooltip(properties) {
    var bits = [ACTIVITY_LABELS[properties.activity] || properties.category];
    if (properties.grade_or_difficulty) bits.push(properties.grade_or_difficulty);
    if (properties.distance_mi) bits.push(formatNumber(properties.distance_mi) + ' mi');
    if (properties.elevation_ft) bits.push(formatNumber(properties.elevation_ft) + ' ft');
    var title = splitTitle(properties.title);
    return (
      '<div class="atlas-tooltip__title">' + escapeHtml(title.main) + '</div>' +
      '<div class="atlas-tooltip__meta">' + escapeHtml(bits.join(' \u00b7 ')) + '</div>' +
      '<div class="atlas-tooltip__hint">Click for route beta</div>'
    );
  }

  // -------------------------------------------------------------------------
  // Controls + legend
  // -------------------------------------------------------------------------

  function availableMetrics() {
    return FILTER_METRICS[state.filter] || ['activity'];
  }

  function rebuildMetricButtons() {
    var toggle = document.querySelector('.atlas-toggle');
    if (!toggle) return;
    toggle.innerHTML = availableMetrics().map(function (metric) {
      var active = metric === state.metric;
      return '<button type="button" class="atlas-toggle__btn' + (active ? ' is-active' : '') +
        '" data-metric="' + metric + '" aria-pressed="' + (active ? 'true' : 'false') + '">' +
        escapeHtml(METRICS[metric].short) + '</button>';
    }).join('');
  }

  function buildControls() {
    var controls = document.createElement('div');
    controls.className = 'atlas-controls';
    controls.setAttribute('role', 'group');
    controls.setAttribute('aria-label', 'Map display options');
    controls.innerHTML =
      '<div class="atlas-toggle" role="group" aria-label="Shade regions by"></div>' +
      '<button type="button" class="atlas-control-btn" data-action="terrain" aria-pressed="false">3D terrain</button>' +
      '<button type="button" class="atlas-control-btn" data-action="reset">Reset view</button>';
    state.container.appendChild(controls);
    rebuildMetricButtons();

    controls.addEventListener('click', function (event) {
      var metricButton = event.target.closest('[data-metric]');
      if (metricButton) {
        setMetric(metricButton.getAttribute('data-metric'));
        return;
      }
      var actionButton = event.target.closest('[data-action]');
      if (!actionButton) return;
      if (actionButton.getAttribute('data-action') === 'terrain') toggleTerrain();
      if (actionButton.getAttribute('data-action') === 'reset') resetView(true);
    });
  }

  function buildLegend() {
    var legend = document.createElement('div');
    legend.className = 'atlas-legend';
    legend.setAttribute('aria-hidden', 'true');
    legend.innerHTML =
      '<span class="atlas-legend__title"></span>' +
      '<span class="atlas-legend__bar"></span>' +
      '<span class="atlas-legend__range"><span class="atlas-legend__min"></span><span class="atlas-legend__max"></span></span>';
    state.container.appendChild(legend);
    state.legend = legend;
    updateLegend();
  }

  function updateLegend() {
    if (!state.legend) return;
    var property = densityProperty();
    var values = (state.density && state.density.features || []).map(function (feature) {
      return Number(feature.properties[property]) || 0;
    });
    var max = Math.max.apply(null, values);
    if (!isFinite(max) || max <= 0) max = 1;
    state.legend.querySelector('.atlas-legend__title').textContent = densityLabel();
    state.legend.querySelector('.atlas-legend__min').textContent = '0';
    state.legend.querySelector('.atlas-legend__max').textContent = formatNumber(max) + ' / cell';
    // The box is the heatmap key, so it uses the same ramp as the heatmap layer.
    // Skip the fully transparent zero stop so the key is legible.
    state.legend.querySelector('.atlas-legend__bar').style.background =
      'linear-gradient(90deg,' + HEATMAP_COLORS.slice(1).join(',') + ')';
  }

  // Keep the green admin shading and the heatmap on exactly the same filter /
  // metric, so switching activity type or shading stat changes both together.
  function refreshMetricPaint() {
    var map = state.map;
    if (map.getLayer('regions-fill')) {
      map.setPaintProperty('regions-fill', 'fill-color', metricExpression(state.metric));
      map.setPaintProperty('regions-fill', 'fill-opacity', regionFillOpacity());
    }
    if (map.getLayer('activity-heat')) {
      map.setPaintProperty('activity-heat', 'heatmap-weight', densityWeightExpression());
    }
  }

  function setMetric(metric) {
    if (!METRICS[metric] || state.metric === metric) return;
    state.metric = metric;
    refreshMetricPaint();
    document.querySelectorAll('.atlas-toggle__btn').forEach(function (button) {
      var active = button.getAttribute('data-metric') === metric;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    updateLegend();
  }

  function toggleTerrain() {
    state.terrain = !state.terrain;
    if (state.terrain) {
      state.map.setTerrain({ source: 'terrain-dem', exaggeration: 1.35 });
      state.map.setLayoutProperty('hillshade', 'visibility', 'visible');
      state.map.easeTo({ pitch: 55, bearing: -18, duration: 900 });
    } else {
      state.map.setTerrain(null);
      state.map.setLayoutProperty('hillshade', 'visibility', 'none');
      state.map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
    }
    var button = document.querySelector('.atlas-control-btn[data-action="terrain"]');
    if (button) {
      button.setAttribute('aria-pressed', state.terrain ? 'true' : 'false');
      button.textContent = state.terrain ? '2D map' : '3D terrain';
    }
  }

  // Start (and reset) on the whole world rather than the geographic midpoint
  // of the activity bounds, which used to fall on France.
  var WORLD_BOUNDS = [[-180, -60], [180, 80]];

  function resetView(animate) {
    state.map.fitBounds(WORLD_BOUNDS, {
      padding: { top: 12, bottom: 12, left: 12, right: 12 },
      duration: animate ? 900 : 0
    });
  }

  // -------------------------------------------------------------------------
  // Drawer
  // -------------------------------------------------------------------------

  function openDrawer(html) {
    if (!state.drawer) return;
    state.lastFocus = document.activeElement;
    state.drawer.querySelector('.atlas-drawer__body').innerHTML = html;
    state.drawer.classList.add('is-open');
    state.drawer.setAttribute('aria-hidden', 'false');
    if (state.backdrop) state.backdrop.hidden = false;
    document.body.classList.add('atlas-drawer-open');
    var close = state.drawer.querySelector('.atlas-drawer__close');
    if (close) close.focus();
  }

  function closeDrawer() {
    if (!state.drawer) return;
    state.drawer.classList.remove('is-open');
    state.drawer.setAttribute('aria-hidden', 'true');
    if (state.backdrop) state.backdrop.hidden = true;
    document.body.classList.remove('atlas-drawer-open');
    if (state.lastFocus && typeof state.lastFocus.focus === 'function') state.lastFocus.focus();
  }

  function routeDrawer(properties) {
    var title = splitTitle(properties.title);
    var stats = [
      { label: 'Difficulty', value: properties.grade_or_difficulty },
      { label: 'Distance', value: properties.distance_mi ? formatNumber(properties.distance_mi) + ' mi' : '' },
      { label: 'Gain', value: properties.elevation_ft ? formatNumber(properties.elevation_ft) + ' ft' : '' },
      { label: 'Season', value: properties.season_year ? String(properties.season_year) : '' }
    ].filter(function (stat) { return stat.value; });

    return (
      '<div class="atlas-drawer__eyebrow">' +
        activityDot(properties.activity) +
        '<span>' + escapeHtml(properties.category) + '</span>' +
        '<span class="atlas-drawer__sep">\u00b7</span>' +
        '<span>' + escapeHtml(properties.region_name) + '</span>' +
      '</div>' +
      (properties.image ? '<img class="atlas-drawer__image" src="' + escapeHtml(properties.image) + '" alt="" loading="lazy">' : '') +
      '<h3 class="atlas-drawer__title" id="atlas-drawer-title">' + escapeHtml(title.main) + '</h3>' +
      (title.detail ? '<p class="atlas-drawer__subtitle">' + escapeHtml(title.detail) + '</p>' : '') +
      '<p class="atlas-drawer__place">' + escapeHtml(properties.location || '') +
        (properties.season_year ? ' \u00b7 ' + escapeHtml(String(properties.season_year)) : '') + '</p>' +
      (stats.length
        ? '<dl class="atlas-drawer__stats">' + stats.map(function (stat) {
            return '<div class="atlas-drawer__stat"><dt>' + escapeHtml(stat.label) + '</dt><dd>' + escapeHtml(stat.value) + '</dd></div>';
          }).join('') + '</dl>'
        : '') +
      (properties.summary ? '<p class="atlas-drawer__summary">' + escapeHtml(properties.summary) + '</p>' : '') +
      (properties.post_url
        ? '<a class="atlas-drawer__cta" href="' + escapeHtml(properties.post_url) + '">Read Activity Story <span aria-hidden="true">\u2192</span></a>'
        : '')
    );
  }

  function regionDrawer(properties) {
    var sports = asArray(properties.sports).map(function (sport) {
      return '<span class="atlas-drawer__sport">' + activityDot(sport) + escapeHtml(ACTIVITY_LABELS[sport] || sport) + '</span>';
    }).join('');
    var stories = asArray(properties.top_stories).filter(function (story) {
      return state.filter === 'all' || story.activity === state.filter;
    }).map(function (story) {
      return '<li><a href="' + escapeHtml(story.post_url) + '">' + escapeHtml(splitTitle(story.title).main) + '</a></li>';
    }).join('');
    var coverage = Math.round((properties.vert_coverage || 0) * 100);
    var stats = regionStats(properties);

    return (
      '<div class="atlas-drawer__eyebrow">' +
        escapeHtml(String(properties.region_kind || 'region').toUpperCase()) + ' REGION</div>' +
      '<h3 class="atlas-drawer__title" id="atlas-drawer-title">' + escapeHtml(properties.region_name) + '</h3>' +
      '<p class="atlas-drawer__place">' + escapeHtml(sports ? 'Activities logged here' : '') + '</p>' +
      '<div class="atlas-drawer__sports">' + sports + '</div>' +
      '<dl class="atlas-drawer__stats">' + stats.map(function (stat) {
        return '<div class="atlas-drawer__stat"><dt>' + escapeHtml(stat.label) + '</dt><dd>' + escapeHtml(stat.value) + '</dd></div>';
      }).join('') + '</dl>' +
      (stories ? '<div class="atlas-drawer__section"><h4>Featured stories</h4><ul class="atlas-drawer__stories">' + stories + '</ul></div>' : '') +
      '<p class="atlas-drawer__note">Logged vert covers ' + coverage + '% of activities; it sums only the activities with a recorded gain.</p>'
    );
  }

  // -------------------------------------------------------------------------
  // Events
  // -------------------------------------------------------------------------

  function wireMapEvents() {
    var map = state.map;

    map.on('mousemove', 'regions-fill', function (event) {
      var feature = event.features && event.features[0];
      if (!feature) return;
      if (state.hoveredRegion !== feature.id) {
        if (state.hoveredRegion !== null) {
          map.setFeatureState({ source: 'regions', id: state.hoveredRegion }, { hover: false });
        }
        state.hoveredRegion = feature.id;
        map.setFeatureState({ source: 'regions', id: feature.id }, { hover: true });
      }
      map.getCanvas().style.cursor = 'pointer';
      showTooltip(event.point, regionTooltip(feature.properties));
    });

    map.on('mouseleave', 'regions-fill', function () {
      map.getCanvas().style.cursor = '';
      if (state.hoveredRegion !== null) {
        map.setFeatureState({ source: 'regions', id: state.hoveredRegion }, { hover: false });
        state.hoveredRegion = null;
      }
      hideTooltip();
    });

    map.on('click', 'regions-fill', function (event) {
      if (map.queryRenderedFeatures(event.point, { layers: ['routes-hit', 'routes-dot'] }).length) return;
      var feature = event.features && event.features[0];
      if (!feature) return;
      var bounds = featureBounds([feature]);
      if (!bounds.isEmpty()) {
        map.fitBounds(bounds, { padding: 70, maxZoom: 9, duration: 900 });
      }
      openDrawer(regionDrawer(feature.properties));
    });

    ['routes-dot', 'routes-hit'].forEach(function (layer) {
      map.on('mousemove', layer, function (event) {
        var feature = event.features && event.features[0];
        if (!feature) return;
        if (state.hoveredRoute !== feature.id) {
          if (state.hoveredRoute !== null) {
            map.setFeatureState({ source: 'routes', id: state.hoveredRoute }, { hover: false });
          }
          state.hoveredRoute = feature.id;
          map.setFeatureState({ source: 'routes', id: feature.id }, { hover: true });
        }
        map.getCanvas().style.cursor = 'pointer';
        showTooltip(event.point, routeTooltip(feature.properties));
      });

      map.on('mouseleave', layer, function () {
        map.getCanvas().style.cursor = '';
        if (state.hoveredRoute !== null) {
          map.setFeatureState({ source: 'routes', id: state.hoveredRoute }, { hover: false });
          state.hoveredRoute = null;
        }
        hideTooltip();
      });

      map.on('click', layer, function (event) {
        var feature = event.features && event.features[0];
        if (!feature) return;
        if (event.originalEvent) event.originalEvent.stopPropagation();
        hideTooltip();
        openDrawer(routeDrawer(feature.properties));
      });
    });

    map.on('movestart', hideTooltip);
  }

  function wireFilters() {
    var buttons = document.querySelectorAll('.map-filter');
    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        applyFilter(button.getAttribute('data-filter') || 'all');
      });
    });
  }

  function applyFilter(value) {
    state.filter = value;
    var filter = value === 'all' ? null : ['==', ['get', 'activity'], value];
    ['routes-halo', 'routes-dot', 'routes-hit'].forEach(function (layer) {
      if (state.map.getLayer(layer)) state.map.setFilter(layer, filter);
    });
    if (availableMetrics().indexOf(state.metric) === -1) state.metric = 'activity';
    rebuildMetricButtons();
    refreshMetricPaint();
    updateLegend();
    document.querySelectorAll('.map-filter').forEach(function (button) {
      var active = button.getAttribute('data-filter') === value;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function wireDrawer() {
    state.drawer = document.getElementById('atlas-drawer');
    state.backdrop = document.getElementById('atlas-backdrop');
    if (!state.drawer) return;
    var close = state.drawer.querySelector('.atlas-drawer__close');
    if (close) close.addEventListener('click', closeDrawer);
    if (state.backdrop) state.backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && state.drawer.classList.contains('is-open')) closeDrawer();
    });
  }

  // -------------------------------------------------------------------------
  // Init
  // -------------------------------------------------------------------------

  function init(containerId, options) {
    options = options || {};
    var container = document.getElementById(containerId);
    if (!container) return null;
    if (!window.maplibregl) {
      container.innerHTML = '<div class="map-status">The map library could not be loaded.</div>';
      return null;
    }

    state.container = container;
    state.tooltip = document.createElement('div');
    state.tooltip.className = 'atlas-tooltip';
    state.tooltip.setAttribute('role', 'status');
    state.tooltip.setAttribute('aria-live', 'polite');
    container.appendChild(state.tooltip);

    var status = document.createElement('div');
    status.className = 'map-status';
    status.textContent = 'Loading the adventure atlas\u2026';
    container.appendChild(status);

    var regionsUrl = options.regionsUrl || '/data/regions.geojson';
    var routesUrl = options.routesUrl || '/data/featured_routes.geojson';
    var densityUrl = options.densityUrl || '/data/activity_density.geojson';

    state.map = new maplibregl.Map({
      container: container,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [0, 20],
      zoom: 1,
      minZoom: 0,
      maxZoom: 15,
      pitchWithRotate: false,
      attributionControl: false
    });

    state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    state.map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'imperial' }), 'bottom-right');
    state.map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    // MapLibre's `load` waits for every basemap tile. If the remote style is
    // slow (or a headless browser cannot render tiles), we can already add our
    // sources on `style.load`, so the atlas does not depend on the basemap.
    var mapReady = new Promise(function (resolve) {
      state.map.once('style.load', resolve);
      state.map.once('load', resolve);
    });

    Promise.all([
      mapReady,
      jsonSource(options.regionsData, regionsUrl),
      jsonSource(options.routesData, routesUrl),
      jsonSource(options.densityData, densityUrl).catch(function () { return null; })
    ])
      .then(function (results) {
        state.regions = results[1];
        state.routes = results[2];
        state.density = results[3];
        addAtlasLayers();
        buildControls();
        buildLegend();
        wireMapEvents();
        resetView(false);
        status.remove();
        state.map.resize();
      })
      .catch(function (error) {
        status.textContent = 'Could not load the atlas data. ' + (error && error.message ? error.message : '');
        status.classList.add('map-status--error');
      });

    wireFilters();
    wireDrawer();

    return {
      map: state.map,
      setFilter: applyFilter,
      setMetric: setMetric,
      reset: function () { resetView(true); }
    };
  }

  window.AtlasMap = { init: init };
})();
