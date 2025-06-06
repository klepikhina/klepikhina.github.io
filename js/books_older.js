  // Set the dimensions and margins of the diagram
  var margin = {top: 10, right: 10, bottom: 10, left: 10},
      width = 1500 - margin.left - margin.right,
      height = 500 - margin.top - margin.bottom;

  // ----------------------------------------
  /* Pan, zoom! */
  // https://www.d3-graph-gallery.com/graph/interactivity_zoom.html
  var svg = d3.select("body").append("svg")
      .attr("width", width + margin.right + margin.left)
      .attr("height", height + margin.top + margin.bottom)
      .call(d3.zoom().on("zoom", function () {
        svg.attr("transform", d3.event.transform)
      }))
    .append("g")
      .attr("transform", "translate("
            + margin.left + "," + margin.top + ")");
// ----------------------------------------

  var i = 0,
      duration = 250,
      root;

  // declares a tree layout and assigns the size
  var treemap = d3.tree().size([height, width]);

  // load the external data
  d3.json("../js/books.json", function(error, treeData) {
    if (error) throw error;

    // Assigns parent, children, height, depth
    root = d3.hierarchy(treeData, function(d) { return d.children; });
    root.x0 = height / 2;
    root.y0 = 0;

    // Collapse after the second level
    root.children.forEach(collapse);

    update(root);

  });

  // Collapse the node and all it's children
  function collapse(d) {
    if(d.children) {
      d._children = d.children
      d._children.forEach(collapse)
      d.children = null
    }
  }

  function update(source) {

    // Assigns the x and y position for the nodes
    var treeData = treemap(root);

    // Compute the new tree layout.
    var nodes = treeData.descendants(),
        links = treeData.descendants().slice(1);

    // Normalize for fixed-depth.
    nodes.forEach(function(d){ d.y = d.depth * 180});

    // ********** Nodes section *******************

    // Update the nodes...
    var node = svg.selectAll('g.node')
        .data(nodes, function(d) {return d.id || (d.id = ++i); });

    // Enter any new modes at the parent's previous position.
    var nodeEnter = node.enter().append('g')
        .attr('class', 'node')
        // --------------------------------------------
        // per answer at
				// https://stackoverflow.com/questions/67480339/programmatically-opening-d3-js-v4-collapsible-tree-nodes
        .attr('node-name', d => d.data.name)
				// --------------------------------------------
        .attr("transform", function(d) {
          return "translate(" + source.y0 + "," + source.x0 + ")";
      })
      .on('click', click);

    // Add Circle for the nodes
    nodeEnter.append('circle')
        .attr('class', 'node')
        .attr('r', 1e-6)
        .style("fill", function(d) {
            return d._children ? "#82ad8b" : "#63856a";
        });

    // Add labels for the nodes
    nodeEnter.append('text')
        .attr("dy", ".35em")
        .attr("x", function(d) {
            return d.children || d._children ? -13 : 13;
        })
        .attr("text-anchor", function(d) {
            return d.children || d._children ? "end" : "start";
        })
        .text(function(d) { return d.data.name; });

    // UPDATE
    var nodeUpdate = nodeEnter.merge(node);

    // Transition to the proper position for the node
    nodeUpdate.transition()
      .duration(duration)
      .attr("transform", function(d) { 
          return "translate(" + d.y + "," + d.x + ")";
      });

    // Update the node attributes and style
    nodeUpdate.select('circle.node')
      .attr('r', 10)
      .style("fill", function(d) {
          return d._children ? "#82ad8b" : "#63856a";
      })
      .attr('cursor', 'pointer');


    // Remove any exiting nodes
    var nodeExit = node.exit().transition()
        .duration(duration)
        .attr("transform", function(d) {
            return "translate(" + source.y + "," + source.x + ")";
        })
        .remove();

    // On exit reduce the node circles size to 0
    nodeExit.select('circle')
      .attr('r', 1e-6);

    // On exit reduce the opacity of text labels
    nodeExit.select('text')
      .style('fill-opacity', 1e-6);

    // ********** links section *******************

    // Update the links...
    var link = svg.selectAll('path.link')
        .data(links, function(d) { return d.id; });

    // Enter any new links at the parent's previous position.
    var linkEnter = link.enter().insert('path', "g")
        .attr("class", "link")
        .attr('d', function(d){
          var o = {x: source.x0, y: source.y0}
          return diagonal(o, o)
        });

    // UPDATE
    var linkUpdate = linkEnter.merge(link);

    // Transition back to the parent element position
    linkUpdate.transition()
        .duration(duration)
        .attr('d', function(d){ return diagonal(d, d.parent) });

    // Remove any exiting links
    var linkExit = link.exit().transition()
        .duration(duration)
        .attr('d', function(d) {
          var o = {x: source.x, y: source.y}
          return diagonal(o, o)
        })
        .remove();

    // Store the old positions for transition.
    nodes.forEach(function(d){
      d.x0 = d.x;
      d.y0 = d.y;
    });

    // Creates a curved (diagonal) path from parent to the child nodes
    function diagonal(s, d) {

      path = `M ${s.y} ${s.x}
              C ${(s.y + d.y) / 2} ${s.x},
                ${(s.y + d.y) / 2} ${d.x},
                ${d.y} ${d.x}`

      return path
    }

    // ----------------------------------------
    // Toggle children on click.

    function click(d) {
      if (d.children) {
        d._children = d.children;
        d.children = null;
      } else if (d._children) {
        d.children = d._children;
        d._children = null;
      } else {
        // This was a leaf node, so redirect.
        console.log('d:', d)
        console.log('d.data:', d.data)
        console.log('d.name:', d.name)
        console.log('d.data.name:', d.data.name)
        console.log('urlMap[d.data.name]:', urlMap[d.data.name])
        window.location = d.data.url;
        window.open("https://www.amazon.com/", d.value);// "_self");
      }
      update(d);
    }
    // ----------------------------------------
  }

// per answer at
// https://stackoverflow.com/questions/67480339/programmatically-opening-d3-js-v4-collapsible-tree-nodes
  setTimeout(() => {
    const node = d3.select('.node[node-name="analytics"]').node();
    console.log('NODE: ', node);
    node.dispatchEvent(new Event('click'));
  }, 2500);
