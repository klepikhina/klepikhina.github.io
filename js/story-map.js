(function () {
  'use strict';

  var source = document.getElementById('story-data');
  if (!source) return;

  var stories = [];
  try {
    stories = JSON.parse(source.textContent.trim()) || [];
  } catch (error) {
    stories = [];
  }
  stories = stories.filter(function (story) {
    return story && story.coordinates && story.coordinates.length === 2;
  });
  if (!stories.length) return;

  var SPORTS = [
    { key: 'bike', label: 'Bike', color: '#C25450' },
    { key: 'climb', label: 'Climb', color: '#D4A574' },
    { key: 'hike', label: 'Hike', color: '#4A7C59' },
    { key: 'run', label: 'Run', color: '#4A90A4' },
    { key: 'ski', label: 'Ski', color: '#8FA6C9' },
    { key: 'water', label: 'Water', color: '#0C589C' }
  ];

  var mapBox = document.getElementById('story-map');
  var status = document.getElementById('story-map-status');
  var regionsBox = document.getElementById('story-regions');
  var cardsBox = document.getElementById('adventures-grid');
  var mapPanel = document.getElementById('adventures-map-panel');
  var cardsPanel = document.getElementById('adventures-cards-panel');
  var viewRadios = document.querySelectorAll('input[name="adventures-view"]');
  if (!mapBox || !status || !regionsBox) return;

  var map = null;
  var markers = [];
  var markerNodes = {};
  var popup = null;
  var selectedSport = 'all';
  var selectedRegion = 'all';
  var selectedId = null;
  var currentView = 'map';

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }

  function sportMeta(key) {
    for (var i = 0; i < SPORTS.length; i += 1) {
      if (SPORTS[i].key === key) return SPORTS[i];
    }
    return { key: key, label: key || 'Other', color: '#BDB7AE' };
  }

  function baseurl() {
    return window.STORY_BASEURL || '';
  }

  function filteredStories() {
    return stories.filter(function (story) {
      return (selectedSport === 'all' || story.activity === selectedSport) &&
        (selectedRegion === 'all' || story.region_id === selectedRegion);
    });
  }

  function byId(id) {
    for (var i = 0; i < stories.length; i += 1) {
      if (stories[i].id === id) return stories[i];
    }
    return null;
  }

  function renderRegions() {
    var sportStories = stories.filter(function (story) {
      return selectedSport === 'all' || story.activity === selectedSport;
    });
    var counts = {};
    sportStories.forEach(function (story) {
      if (!story.region_id) return;
      if (!counts[story.region_id]) {
        counts[story.region_id] = {
          id: story.region_id,
          name: story.region_name || story.region_id,
          count: 0
        };
      }
      counts[story.region_id].count += 1;
    });
    var regions = Object.keys(counts)
      .map(function (key) { return counts[key]; })
      .sort(function (a, b) { return b.count - a.count || a.name.localeCompare(b.name); });

    regionsBox.textContent = '';
    var entries = [{ id: 'all', name: 'All places', count: sportStories.length }].concat(regions);
    entries.forEach(function (entry) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'story-region' + (selectedRegion === entry.id ? ' is-active' : '');
      button.dataset.region = entry.id;
      button.setAttribute('aria-pressed', String(selectedRegion === entry.id));
      button.innerHTML = '<span>' + escapeHtml(entry.name) + '</span><small>' + entry.count + '</small>';
      button.addEventListener('click', function () { setRegion(entry.id); });
      regionsBox.appendChild(button);
    });
  }

  function clearMarkers() {
    markers.forEach(function (marker) { marker.remove(); });
    markers = [];
    markerNodes = {};
  }

  function renderMarkers() {
    if (!map) return;
    clearMarkers();
    filteredStories().forEach(function (story) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'story-pin' + (selectedId === story.id ? ' is-active' : '');
      button.style.setProperty('--pin-color', sportMeta(story.activity).color);
      button.title = story.title + ' — ' + (story.location || story.region_name || '');
      button.setAttribute('aria-label', button.title);
      var image = document.createElement('img');
      image.src = story.pin_image || story.image;
      image.alt = '';
      image.loading = 'lazy';
      image.decoding = 'async';
      button.appendChild(image);
      button.addEventListener('click', function () { openStory(story.id, true); });
      markerNodes[story.id] = button;
      markers.push(new maplibregl.Marker({ element: button, anchor: 'center' })
        .setLngLat(story.coordinates)
        .addTo(map));
    });
  }

  function closePopup() {
    if (popup) {
      popup.remove();
      popup = null;
    }
  }

  function openStory(id, fly) {
    var story = byId(id);
    if (!story || !map) return;
    selectedId = id;
    Object.keys(markerNodes).forEach(function (key) {
      markerNodes[key].classList.toggle('is-active', key === id);
    });

    var sport = sportMeta(story.activity);
    var place = story.location || story.region_name || '';
    var html =
      '<a class="story-popup__link" href="' + escapeHtml(baseurl() + story.post_url) + '">' +
        '<span class="story-popup__media">' +
          '<img src="' + escapeHtml(story.card_image || story.image) + '" alt="" loading="lazy">' +
          '<span class="story-popup__sport" style="background:' + escapeHtml(sport.color) + '">' +
            escapeHtml(sport.label) + '</span>' +
        '</span>' +
        '<span class="story-popup__body">' +
          '<strong>' + escapeHtml(story.title) + '</strong>' +
          '<small>' + escapeHtml(place) + '</small>' +
          '<em>Read the story &rarr;</em>' +
        '</span>' +
      '</a>';

    closePopup();
    popup = new maplibregl.Popup({
      offset: 20,
      maxWidth: '244px',
      closeButton: false,
      closeOnClick: false,
      className: 'story-popup-wrap'
    }).setLngLat(story.coordinates).setHTML(html).addTo(map);

    if (fly) map.flyTo({ center: story.coordinates, zoom: 10, essential: true });
  }

  function filterCards() {
    if (!cardsBox) return;
    var visible = {};
    filteredStories().forEach(function (story) { visible[story.id] = true; });
    Array.prototype.forEach.call(cardsBox.children, function (item) {
      item.style.display = visible[item.dataset.id] ? '' : 'none';
    });
  }

  function setView(view) {
    currentView = view;
    if (mapPanel) mapPanel.style.display = view === 'map' ? '' : 'none';
    if (cardsPanel) cardsPanel.style.display = view === 'cards' ? '' : 'none';
    Array.prototype.forEach.call(viewRadios, function (radio) {
      radio.checked = radio.value === view;
    });
    if (view === 'map' && map) {
      map.resize();
    } else {
      closePopup();
    }
  }

  function fitStories(visible) {
    if (!map || !visible.length || !window.maplibregl) return;
    var bounds = new maplibregl.LngLatBounds();
    visible.forEach(function (story) { bounds.extend(story.coordinates); });
    map.fitBounds(bounds, { padding: 72, maxZoom: 10, duration: 800 });
  }

  function applyFilters(fit) {
    var visible = filteredStories();
    if (!visible.length || !byId(selectedId) || visible.indexOf(byId(selectedId)) === -1) {
      selectedId = null;
      closePopup();
    }
    renderRegions();
    renderMarkers();
    if (markers.length && status && !status.hidden) status.hidden = true;
    filterCards();
    if (fit && currentView === 'map') fitStories(visible);
  }

  function setSport(sport) {
    selectedSport = sport;
    selectedRegion = 'all';
    Array.prototype.forEach.call(document.querySelectorAll('.story-filters .filter-pill'), function (button) {
      var active = button.dataset.sport === sport;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    applyFilters(true);
  }

  function setRegion(region) {
    selectedRegion = region;
    applyFilters(true);
  }

  Array.prototype.forEach.call(document.querySelectorAll('.story-filters .filter-pill'), function (button) {
    button.addEventListener('click', function () { setSport(button.dataset.sport); });
  });
  Array.prototype.forEach.call(viewRadios, function (radio) {
    radio.addEventListener('change', function () { setView(radio.value); });
  });
  setView('map');

  if (!window.maplibregl) {
    setView('cards');
    applyFilters(false);
    return;
  }

  try {
    map = new maplibregl.Map({
      container: mapBox,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [-105, 42],
      zoom: 3.2,
      attributionControl: true
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'imperial' }), 'bottom-right');
  } catch (error) {
    // Some browsers block WebGL. The map cannot render there, so show the
    // same stories as cards instead of leaving a stuck loading message.
    status.hidden = true;
    var mapRadio = document.getElementById('view-map');
    var mapLabel = document.querySelector('.view-toggle label[for="view-map"]');
    if (mapRadio) mapRadio.disabled = true;
    if (mapLabel) {
      mapLabel.classList.add('is-disabled');
      mapLabel.title = 'The photo map needs WebGL, which is unavailable in this browser.';
    }
    setView('cards');
    applyFilters(false);
    return;
  }
  map.on('load', function () {
    status.hidden = true;
    applyFilters(true);
  });
  map.on('error', function () {
    if (status.hidden) return;
    status.textContent = 'Basemap unavailable; switch to Cards to browse.';
    status.classList.add('map-status--error');
    applyFilters(false);
  });

  applyFilters(false);
})();
