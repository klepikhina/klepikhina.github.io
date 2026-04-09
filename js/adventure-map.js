/**
 * Adventure Map - D3.js Interactive World Map
 * Displays adventure locations with colored markers by activity type
 */

(function() {
  'use strict';

  // Activity colors matching the CSS
  const activityColors = {
    climb: '#D4A574',
    bike: '#6B8E23',
    hike: '#4A7C59',
    run: '#4A90A4',
    ski: '#B0C4DE'
  };

  // Initialize the map
  function initAdventureMap(containerId, adventures) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const width = container.clientWidth;
    const height = Math.min(500, width * 0.6);

    // Clear any existing content
    container.innerHTML = '';

    // Create SVG
    const svg = d3.select('#' + containerId)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('preserveAspectRatio', 'xMidYMid meet');

    // Create projection - focused on North America and Europe
    const projection = d3.geoNaturalEarth1()
      .scale(width / 4.5)
      .center([-100, 45])
      .translate([width / 2, height / 2]);

    const path = d3.geoPath().projection(projection);

    // Create tooltip
    const tooltip = d3.select('body')
      .append('div')
      .attr('class', 'map-tooltip')
      .style('opacity', 0)
      .style('position', 'absolute')
      .style('pointer-events', 'none');

    // Load world map data
    d3.json('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json')
      .then(function(world) {
        // Draw countries
        svg.append('g')
          .attr('class', 'countries')
          .selectAll('path')
          .data(topojson.feature(world, world.objects.countries).features)
          .enter()
          .append('path')
          .attr('d', path)
          .attr('fill', '#E8E4DF')
          .attr('stroke', '#fff')
          .attr('stroke-width', 0.5);

        // Group adventures by location to handle overlapping markers
        const locationGroups = {};
        adventures.forEach(function(adv) {
          const key = adv.latitude + ',' + adv.longitude;
          if (!locationGroups[key]) {
            locationGroups[key] = [];
          }
          locationGroups[key].push(adv);
        });

        // Draw adventure markers
        const markers = svg.append('g')
          .attr('class', 'markers')
          .selectAll('circle')
          .data(adventures)
          .enter()
          .append('circle')
          .attr('cx', function(d) {
            return projection([d.longitude, d.latitude])[0];
          })
          .attr('cy', function(d) {
            return projection([d.longitude, d.latitude])[1];
          })
          .attr('r', 6)
          .attr('fill', function(d) {
            return activityColors[d.activity] || '#666';
          })
          .attr('stroke', '#fff')
          .attr('stroke-width', 1.5)
          .attr('class', function(d) {
            return 'marker marker--' + d.activity;
          })
          .style('cursor', 'pointer')
          .on('mouseover', function(event, d) {
            d3.select(this)
              .transition()
              .duration(200)
              .attr('r', 10);

            tooltip.transition()
              .duration(200)
              .style('opacity', 1);

            tooltip.html(
              '<div class="tooltip-title">' + d.title + '</div>' +
              '<div class="tooltip-location">' + d.location +
              (d.region ? ', ' + d.region : '') + '</div>'
            )
              .style('left', (event.pageX + 10) + 'px')
              .style('top', (event.pageY - 28) + 'px');
          })
          .on('mouseout', function() {
            d3.select(this)
              .transition()
              .duration(200)
              .attr('r', 6);

            tooltip.transition()
              .duration(500)
              .style('opacity', 0);
          })
          .on('click', function(event, d) {
            window.location.href = d.url;
          });

        // Add zoom functionality
        const zoom = d3.zoom()
          .scaleExtent([1, 8])
          .on('zoom', function(event) {
            svg.selectAll('g').attr('transform', event.transform);
          });

        svg.call(zoom);
      })
      .catch(function(error) {
        console.error('Error loading map data:', error);
        container.innerHTML = '<p class="text-center">Map could not be loaded. Please try refreshing the page.</p>';
      });
  }

  // Filter markers by activity type
  function filterMarkers(activity) {
    const markers = d3.selectAll('.marker');

    if (activity === 'all') {
      markers.style('opacity', 1);
    } else {
      markers.style('opacity', function() {
        return d3.select(this).classed('marker--' + activity) ? 1 : 0.2;
      });
    }

    // Update filter buttons
    document.querySelectorAll('.map-filter').forEach(function(btn) {
      btn.classList.remove('active');
    });
    document.querySelector('.map-filter[data-filter="' + activity + '"]').classList.add('active');
  }

  // Expose to global scope
  window.AdventureMap = {
    init: initAdventureMap,
    filter: filterMarkers
  };
})();
