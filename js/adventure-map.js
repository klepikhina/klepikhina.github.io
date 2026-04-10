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

  // Country ID to name mapping (ISO 3166-1 numeric codes used in world-atlas)
  const countryIds = {
    '250': 'France', '380': 'Italy', '756': 'Switzerland', '352': 'Iceland',
    '826': 'United Kingdom', '276': 'Germany', '724': 'Spain', '620': 'Portugal',
    '40': 'Austria', '56': 'Belgium', '528': 'Netherlands', '208': 'Denmark',
    '578': 'Norway', '752': 'Sweden', '246': 'Finland', '616': 'Poland',
    '203': 'Czech Republic', '300': 'Greece', '792': 'Turkey', '392': 'Japan',
    '156': 'China', '356': 'India', '36': 'Australia', '554': 'New Zealand',
    '484': 'Mexico', '76': 'Brazil', '32': 'Argentina', '152': 'Chile',
    '170': 'Colombia', '604': 'Peru', '218': 'Ecuador', '858': 'Uruguay',
    '862': 'Venezuela', '68': 'Bolivia', '600': 'Paraguay',
    '124': 'Canada', '840': 'United States of America'
  };

  // Initialize the map
  function initAdventureMap(containerId, adventures) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const width = container.clientWidth;
    const height = Math.min(600, width * 0.55);

    // Clear any existing content
    container.innerHTML = '';

    // Create SVG
    const svg = d3.select('#' + containerId)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .style('background', '#f5f5f5')
      .style('overflow', 'hidden');

    // Main group for all map content (will be transformed on zoom)
    const mapGroup = svg.append('g')
      .attr('class', 'map-group');

    // Create projection - show full globe, scale to fill container
    const projection = d3.geoNaturalEarth1()
      .scale(width / 5)
      .center([0, 15])
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

    // Collect visited regions and countries from adventures
    const visitedRegions = new Set();
    const visitedCountries = new Set();
    const regionActivities = {};

    adventures.forEach(function(adv) {
      if (adv.region) {
        // Handle comma-separated regions
        const regions = adv.region.split(',').map(r => r.trim());
        regions.forEach(region => {
          visitedRegions.add(region);
          if (!regionActivities[region]) {
            regionActivities[region] = new Set();
          }
          regionActivities[region].add(adv.activity);
        });
      }
      if (adv.country) {
        // Handle comma-separated countries
        const countries = adv.country.split(',').map(c => c.trim());
        countries.forEach(country => {
          visitedCountries.add(country);
          if (!regionActivities[country]) {
            regionActivities[country] = new Set();
          }
          regionActivities[country].add(adv.activity);
        });
      }
    });

    // Function to get URL slug for a region
    function getRegionSlug(name) {
      return name.toLowerCase().replace(/\s+/g, '-');
    }

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

      // Countries with overseas territories that should only highlight the main territory
      // Format: countryId -> [minLon, maxLon, minLat, maxLat] for main territory
      const countryBounds = {
        '250': [-5, 10, 41, 51],      // France (metropolitan only, excludes French Guiana, etc.)
        '528': [3, 8, 50, 54],        // Netherlands (excludes Caribbean)
        '826': [-8, 2, 49, 61]        // UK (excludes overseas territories)
      };

      // Function to check if a point is within bounds
      function isInBounds(lon, lat, bounds) {
        return lon >= bounds[0] && lon <= bounds[1] && lat >= bounds[2] && lat <= bounds[3];
      }

      // Function to check if a country is visited and get its fill
      function getCountryFill(countryId, centroid) {
        const countryName = countryIds[countryId];
        if (countryName && (visitedCountries.has(countryName) || visitedRegions.has(countryName))) {
          // Check if this territory should be filtered by bounds
          if (countryBounds[countryId] && centroid) {
            if (!isInBounds(centroid[0], centroid[1], countryBounds[countryId])) {
              return '#E8E4DF'; // Don't highlight overseas territories
            }
          }
          const activities = regionActivities[countryName];
          if (activities && activities.size > 0) {
            const firstActivity = activities.values().next().value;
            return activityColorsLight[firstActivity] || 'rgba(74, 124, 89, 0.3)';
          }
          return 'rgba(74, 124, 89, 0.3)';
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
        .attr('fill', d => {
          // Get centroid for bounds checking (in geographic coordinates)
          const centroid = d3.geoCentroid(d);
          return getCountryFill(d.id, centroid);
        })
        .attr('stroke', '#ccc')
        .attr('stroke-width', 0.5)
        .attr('class', d => {
          const countryName = countryIds[d.id];
          const centroid = d3.geoCentroid(d);
          // Check bounds for countries with overseas territories
          if (countryBounds[d.id] && !isInBounds(centroid[0], centroid[1], countryBounds[d.id])) {
            return '';
          }
          if (countryName && (visitedCountries.has(countryName) || visitedRegions.has(countryName))) {
            return 'region-visited';
          }
          return '';
        })
        .attr('data-region', d => countryIds[d.id] || '')
        .style('cursor', d => {
          const countryName = countryIds[d.id];
          const centroid = d3.geoCentroid(d);
          // Check bounds for countries with overseas territories
          if (countryBounds[d.id] && !isInBounds(centroid[0], centroid[1], countryBounds[d.id])) {
            return 'default';
          }
          return (countryName && (visitedCountries.has(countryName) || visitedRegions.has(countryName))) ? 'pointer' : 'default';
        })
        .on('mouseover', function(event, d) {
          const countryName = countryIds[d.id];
          const centroid = d3.geoCentroid(d);
          // Check bounds for countries with overseas territories
          if (countryBounds[d.id] && !isInBounds(centroid[0], centroid[1], countryBounds[d.id])) {
            return; // Don't highlight overseas territories
          }
          if (countryName && (visitedCountries.has(countryName) || visitedRegions.has(countryName))) {
            // Use vibrant color on hover
            const activities = regionActivities[countryName];
            if (activities && activities.size > 0) {
              const firstActivity = activities.values().next().value;
              d3.select(this).attr('fill', activityColors[firstActivity] || '#4A7C59');
            }
            tooltip.transition().duration(200).style('opacity', 1);
            tooltip.html('<div style="font-weight: 600; color: #2D5A3D;">' + countryName + '</div>')
              .style('left', (event.pageX + 10) + 'px')
              .style('top', (event.pageY - 28) + 'px');
          }
        })
        .on('mouseout', function(event, d) {
          const centroid = d3.geoCentroid(d);
          // Restore to light color
          d3.select(this).attr('fill', getCountryFill(d.id, centroid));
          tooltip.transition().duration(500).style('opacity', 0);
        })
        .on('click', function(event, d) {
          const countryName = countryIds[d.id];
          const centroid = d3.geoCentroid(d);
          // Check bounds for countries with overseas territories
          if (countryBounds[d.id] && !isInBounds(centroid[0], centroid[1], countryBounds[d.id])) {
            return; // Don't navigate for overseas territories
          }
          if (countryName && (visitedCountries.has(countryName) || visitedRegions.has(countryName))) {
            window.location.href = '/regions/' + getRegionSlug(countryName) + '/';
          }
        });

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
        .attr('stroke', '#bbb')
        .attr('stroke-width', 0.5)
        .attr('class', d => {
          const stateName = stateFips[d.id];
          return visitedRegions.has(stateName) ? 'region-visited' : '';
        })
        .attr('data-region', d => stateFips[d.id] || '')
        .style('cursor', d => visitedRegions.has(stateFips[d.id]) ? 'pointer' : 'default')
        .on('mouseover', function(event, d) {
          const stateName = stateFips[d.id];
          if (visitedRegions.has(stateName)) {
            // Use vibrant color on hover
            const activities = regionActivities[stateName];
            if (activities && activities.size > 0) {
              const firstActivity = activities.values().next().value;
              d3.select(this).attr('fill', activityColors[firstActivity] || '#4A7C59');
            }
            tooltip.transition().duration(200).style('opacity', 1);
            tooltip.html('<div style="font-weight: 600; color: #2D5A3D;">' + stateName + '</div>')
              .style('left', (event.pageX + 10) + 'px')
              .style('top', (event.pageY - 28) + 'px');
          }
        })
        .on('mouseout', function(event, d) {
          const stateName = stateFips[d.id];
          // Restore to light color
          d3.select(this).attr('fill', getRegionFill(stateName));
          tooltip.transition().duration(500).style('opacity', 0);
        })
        .on('click', function(event, d) {
          const stateName = stateFips[d.id];
          if (visitedRegions.has(stateName)) {
            window.location.href = '/regions/' + getRegionSlug(stateName) + '/';
          }
        });

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
          .attr('stroke', '#bbb')
          .attr('stroke-width', 0.5)
          .attr('class', d => visitedRegions.has(d.properties.name) ? 'region-visited' : '')
          .attr('data-region', d => d.properties.name || '')
          .style('cursor', d => visitedRegions.has(d.properties.name) ? 'pointer' : 'default')
          .on('mouseover', function(event, d) {
            const provinceName = d.properties.name;
            if (visitedRegions.has(provinceName)) {
              // Use vibrant color on hover
              const activities = regionActivities[provinceName];
              if (activities && activities.size > 0) {
                const firstActivity = activities.values().next().value;
                d3.select(this).attr('fill', activityColors[firstActivity] || '#4A7C59');
              }
              tooltip.transition().duration(200).style('opacity', 1);
              tooltip.html('<div style="font-weight: 600; color: #2D5A3D;">' + provinceName + '</div>')
                .style('left', (event.pageX + 10) + 'px')
                .style('top', (event.pageY - 28) + 'px');
            }
          })
          .on('mouseout', function(event, d) {
            const provinceName = d.properties.name;
            // Restore to light color
            d3.select(this).attr('fill', getRegionFill(provinceName));
            tooltip.transition().duration(500).style('opacity', 0);
          })
          .on('click', function(event, d) {
            const provinceName = d.properties.name;
            if (visitedRegions.has(provinceName)) {
              window.location.href = '/regions/' + getRegionSlug(provinceName) + '/';
            }
          });
      }

      // Draw state/province boundaries
      mapGroup.append('path')
        .datum(topojson.mesh(usData, usData.objects.states, (a, b) => a !== b))
        .attr('class', 'state-boundaries')
        .attr('fill', 'none')
        .attr('stroke', '#bbb')
        .attr('stroke-width', 0.5)
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
      // Allow panning beyond initial view to see full globe
      const zoom = d3.zoom()
        .scaleExtent([0.8, 20])
        .translateExtent([[-width * 0.5, -height * 0.5], [width * 1.5, height * 1.5]]) // Allow panning to see full globe
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
            .attr('stroke-width', 0.5 / currentZoomScale);

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
