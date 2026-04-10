/**
 * Adventure Map - D3.js Interactive World Map
 * Displays adventure locations with colored markers by activity type
 * Features: state/province boundaries, visited region shading, proper zoom scaling
 */

(function() {
  'use strict';

  // Activity colors matching the CSS
  const activityColors = {
    climb: '#D4A574',
    bike: '#6B8E23',
    hike: '#4A7C59',
    run: '#4A90A4',
    ski: '#B0B9DE'
  };

  // Lighter versions for region shading
  const activityColorsLight = {
    climb: 'rgba(212, 165, 116, 0.25)',
    bike: 'rgba(107, 142, 35, 0.25)',
    hike: 'rgba(74, 124, 89, 0.25)',
    run: 'rgba(74, 144, 164, 0.25)',
    ski: 'rgba(176, 185, 222, 0.25)'
  };

  let currentZoomScale = 1;
  const baseMarkerRadius = 6;

  // US State FIPS codes to names mapping
  const stateFips = {
    '01': 'Alabama', '02': 'Alaska', '04': 'Arizona', '05': 'Arkansas',
    '06': 'California', '08': 'Colorado', '09': 'Connecticut', '10': 'Delaware',
    '11': 'District of Columbia', '12': 'Florida', '13': 'Georgia', '15': 'Hawaii',
    '16': 'Idaho', '17': 'Illinois', '18': 'Indiana', '19': 'Iowa',
    '20': 'Kansas', '21': 'Kentucky', '22': 'Louisiana', '23': 'Maine',
    '24': 'Maryland', '25': 'Massachusetts', '26': 'Michigan', '27': 'Minnesota',
    '28': 'Mississippi', '29': 'Missouri', '30': 'Montana', '31': 'Nebraska',
    '32': 'Nevada', '33': 'New Hampshire', '34': 'New Jersey', '35': 'New Mexico',
    '36': 'New York', '37': 'North Carolina', '38': 'North Dakota', '39': 'Ohio',
    '40': 'Oklahoma', '41': 'Oregon', '42': 'Pennsylvania', '44': 'Rhode Island',
    '45': 'South Carolina', '46': 'South Dakota', '47': 'Tennessee', '48': 'Texas',
    '49': 'Utah', '50': 'Vermont', '51': 'Virginia', '53': 'Washington',
    '54': 'West Virginia', '55': 'Wisconsin', '56': 'Wyoming'
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
      .style('background', '#f5f5f5');

    // Create a clip path to contain the map
    svg.append('defs')
      .append('clipPath')
      .attr('id', 'map-clip')
      .append('rect')
      .attr('width', width)
      .attr('height', height);

    // Main group for all map content (will be transformed on zoom)
    const mapGroup = svg.append('g')
      .attr('class', 'map-group')
      .attr('clip-path', 'url(#map-clip)');

    // Create projection - focused on North America
    const projection = d3.geoNaturalEarth1()
      .scale(width / 4.5)
      .center([-100, 45])
      .translate([width / 2, height / 2]);

    const path = d3.geoPath().projection(projection);

    // Create tooltip
    let tooltip = d3.select('.map-tooltip');
    if (tooltip.empty()) {
      tooltip = d3.select('body')
        .append('div')
        .attr('class', 'map-tooltip')
        .style('opacity', 0)
        .style('position', 'absolute')
        .style('pointer-events', 'none')
        .style('background', '#fff')
        .style('padding', '8px 12px')
        .style('border-radius', '4px')
        .style('box-shadow', '0 2px 8px rgba(0,0,0,0.15)')
        .style('font-size', '13px')
        .style('z-index', '1000');
    }

    // Collect visited regions from adventures
    const visitedRegions = new Set();
    const regionActivities = {};

    adventures.forEach(function(adv) {
      if (adv.region) {
        visitedRegions.add(adv.region);
        if (!regionActivities[adv.region]) {
          regionActivities[adv.region] = new Set();
        }
        regionActivities[adv.region].add(adv.activity);
      }
    });

    // Load map data - US states and world countries
    Promise.all([
      d3.json('https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json'),
      d3.json('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json'),
      d3.json('https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/canada.geojson')
    ]).then(function([usData, worldData, canadaData]) {

      // Function to get fill color for a region
      function getRegionFill(regionName) {
        if (visitedRegions.has(regionName)) {
          const activities = regionActivities[regionName];
          if (activities && activities.size > 0) {
            // Use the first activity's color
            const firstActivity = activities.values().next().value;
            return activityColorsLight[firstActivity] || 'rgba(74, 124, 89, 0.2)';
          }
          return 'rgba(74, 124, 89, 0.2)';
        }
        return '#E8E4DF';
      }

      // Draw world countries first (as base layer)
      mapGroup.append('g')
        .attr('class', 'countries')
        .selectAll('path')
        .data(topojson.feature(worldData, worldData.objects.countries).features)
        .enter()
        .append('path')
        .attr('d', path)
        .attr('fill', '#E8E4DF')
        .attr('stroke', '#ccc')
        .attr('stroke-width', 0.5);

      // Draw US states
      const usStates = topojson.feature(usData, usData.objects.states).features;
      mapGroup.append('g')
        .attr('class', 'us-states')
        .selectAll('path')
        .data(usStates)
        .enter()
        .append('path')
        .attr('d', path)
        .attr('fill', function(d) {
          const stateName = stateFips[d.id] || '';
          return getRegionFill(stateName);
        })
        .attr('stroke', '#999')
        .attr('stroke-width', 0.5);

      // Draw Canadian provinces
      if (canadaData && canadaData.features) {
        mapGroup.append('g')
          .attr('class', 'canada-provinces')
          .selectAll('path')
          .data(canadaData.features)
          .enter()
          .append('path')
          .attr('d', path)
          .attr('fill', function(d) {
            const provinceName = d.properties.name || '';
            return getRegionFill(provinceName);
          })
          .attr('stroke', '#999')
          .attr('stroke-width', 0.5);
      }

      // Draw state/province boundaries with slightly darker stroke
      mapGroup.append('path')
        .datum(topojson.mesh(usData, usData.objects.states, (a, b) => a !== b))
        .attr('class', 'state-boundaries')
        .attr('fill', 'none')
        .attr('stroke', '#888')
        .attr('stroke-width', 0.75)
        .attr('d', path);

      // Markers group - separate so we can scale independently
      const markersGroup = mapGroup.append('g')
        .attr('class', 'markers');

      // Draw adventure markers
      const markers = markersGroup.selectAll('circle')
        .data(adventures)
        .enter()
        .append('circle')
        .attr('cx', function(d) {
          return projection([d.longitude, d.latitude])[0];
        })
        .attr('cy', function(d) {
          return projection([d.longitude, d.latitude])[1];
        })
        .attr('r', baseMarkerRadius)
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
            .attr('r', (baseMarkerRadius * 1.5) / currentZoomScale);

          tooltip.transition()
            .duration(200)
            .style('opacity', 1);

          tooltip.html(
            '<div style="font-weight: 600; color: #2D5A3D;">' + d.title + '</div>' +
            '<div style="color: #666; font-size: 12px;">' + d.location +
            (d.region ? ', ' + d.region : '') + '</div>'
          )
            .style('left', (event.pageX + 10) + 'px')
            .style('top', (event.pageY - 28) + 'px');
        })
        .on('mouseout', function() {
          d3.select(this)
            .transition()
            .duration(200)
            .attr('r', baseMarkerRadius / currentZoomScale);

          tooltip.transition()
            .duration(500)
            .style('opacity', 0);
        })
        .on('click', function(event, d) {
          window.location.href = d.url;
        });

      // Add zoom functionality with proper scaling
      const zoom = d3.zoom()
        .scaleExtent([1, 12])
        .translateExtent([[0, 0], [width, height]]) // Limit panning to map bounds
        .on('zoom', function(event) {
          currentZoomScale = event.transform.k;

          // Transform the map
          mapGroup.attr('transform', event.transform);

          // Scale markers inversely to maintain consistent screen size
          markers
            .attr('r', baseMarkerRadius / currentZoomScale)
            .attr('stroke-width', 1.5 / currentZoomScale);

          // Scale state boundaries
          mapGroup.selectAll('.state-boundaries')
            .attr('stroke-width', 0.75 / currentZoomScale);

          mapGroup.selectAll('.us-states path, .canada-provinces path')
            .attr('stroke-width', 0.5 / currentZoomScale);

          mapGroup.selectAll('.countries path')
            .attr('stroke-width', 0.5 / currentZoomScale);
        });

      svg.call(zoom);

      // Double-click to reset zoom
      svg.on('dblclick.zoom', function() {
        svg.transition()
          .duration(500)
          .call(zoom.transform, d3.zoomIdentity);
      });

    }).catch(function(error) {
      console.error('Error loading map data:', error);
      container.innerHTML = '<p class="text-center" style="padding: 2rem; color: #666;">Map could not be loaded. Please try refreshing the page.</p>';
    });
  }

  // Filter markers by activity type
  function filterMarkers(activity) {
    const markers = d3.selectAll('.marker');

    if (activity === 'all') {
      markers.style('opacity', 1);
    } else {
      markers.style('opacity', function() {
        return d3.select(this).classed('marker--' + activity) ? 1 : 0.15;
      });
    }

    // Update filter buttons
    document.querySelectorAll('.map-filter').forEach(function(btn) {
      btn.classList.remove('active');
    });
    const activeBtn = document.querySelector('.map-filter[data-filter="' + activity + '"]');
    if (activeBtn) activeBtn.classList.add('active');
  }

  // Expose to global scope
  window.AdventureMap = {
    init: initAdventureMap,
    filter: filterMarkers
  };
})();
