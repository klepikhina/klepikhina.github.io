class NetworkChart {
  constructor() {
    const attrs = {
      id: 'ID' + Math.floor(Math.random() * 1000000),
      svgWidth: 975,
      svgHeight: 975,
      marginTop: 5,
      marginBottom: 5,
      marginRight: 5,
      marginLeft: 5,
      container: 'body',
      defaultTextFill: '#2C3E50',
      defaultFont: 'Helvetica',
      duration: 500,
      initialDuration: 500,
      slowDuration: 2000,
      firstRender: true,
      initialLinkColor: '#555',
      normalCircleRadius: 33,
      rootCircleRadius: 50,
      isTree: false,
      transitionEase: d3.easeBack,
      dropShadowId: 'drop-shadow-id',
      onNodeMouseEnter: d => d,
      onNodeMouseLeave: d => d,
      data: null
    };
    this.getState = () => attrs;
    this.setState = d => Object.assign(attrs, d);
    Object.keys(attrs).forEach(key => {
      if (!this[key]) {
        //@ts-ignore
        this[key] = function(_) {
          var string = `attrs['${key}'] = _`;
          if (!arguments.length) {
            return eval(`attrs['${key}'];`);
          }
          eval(string);
          return this;
        };
      }
    });
    this.initializeEnterExitUpdatePattern();
  }

  setAutoBox({ svg }) {
    const node = svg.node();
    const { x, y, width, height } = node.getBBox();
    const result = [x, y, width, height];
    svg.attr('viewBox', result);
  }

  initializeEnterExitUpdatePattern() {
    d3.selection.prototype.patternify = function(params) {
      var container = this;
      var selector = params.selector;
      var elementTag = params.tag;
      var data = params.data || [selector];
      // Pattern in action
      var selection = container.selectAll('.' + selector).data(data, (d, i) => {
        if (typeof d === 'object') {
          if (d.id) {
            return d.id;
          }
        }
        return i;
      });
      selection.exit().remove();
      selection = selection
        .enter()
        .append(elementTag)
        .merge(selection);
      selection.attr('class', selector);
      return selection;
    };
  }

  // ================== RENDERING  ===================
  render() {
    const state = this.getState();

    this.setDynamicContainer(state);
    this.calculateProperties(state);
    this.createTree(state);
    this.computeNodeAndLinkData(state);
    this.drawSvgAndWrappers(state);
    this.createShadowsAndGradients();
    this.drawLinks(state);
    this.drawNodes(state);
    this.setAutoBox(state);
    this.setState({ firstRender: false });

    return this;
  }

  setDynamicContainer() {
    const attrs = this.getState();

    //Drawing containers
    var container = d3.select(attrs.container);
    var containerRect = container.node().getBoundingClientRect();
    //if (containerRect.width > 0) attrs.svgWidth = containerRect.width;
    this.setState({ container });
  }
  calculateProperties() {
    const attrs = this.getState();

    //Calculated properties
    var calc = {
      id: null,
      chartTopMargin: null,
      chartLeftMargin: null,
      chartWidth: null,
      chartHeight: null
    };
    calc.id = 'ID' + Math.floor(Math.random() * 1000000); // id for event handlings
    calc.chartLeftMargin = attrs.marginLeft;
    calc.chartTopMargin = attrs.marginTop;
    calc.chartWidth = attrs.svgWidth - attrs.marginRight - calc.chartLeftMargin;
    calc.chartHeight =
      attrs.svgHeight - attrs.marginBottom - calc.chartTopMargin;
    calc.radius = calc.chartWidth / 2;
    calc.centerX = calc.chartWidth / 2;
    calc.centerY = calc.chartHeight / 2;
    if (attrs.svgWidth < 850) {
      this.setState({ normalCircleRadius: 26 });
      this.setState({ rootCircleRadius: 40 });
    }

    this.setState({ calc });
  }

  // Retrieve links  and nodes
  computeNodeAndLinkData({ root }) {
    const nodesData = root.descendants();
    const linksData = root.links();
    this.setState({ nodesData, linksData });
  }

  createId() {
    return (
      Date.now().toString(36) +
      Math.random()
        .toString(36)
        .substr(2)
    );
  }

  // Create shadows for lines and gradient for hover lines
  createShadowsAndGradients() {
    const { svg, dropShadowId } = this.getState();

    // Initialize shadow properties
    const color = '#66FCF1';
    const opacity = 0.2;
    const filterX = -30;
    const filterY = -30;
    const filterWidth = 400;
    const filterHeight = 400;
    const feOffsetDx = 0;
    const feOffsetDy = 0;
    const feOffsetX = -100;
    const feOffsetY = -100;
    const feGaussianBlurStdDeviation = 10.1;

    // Add Gradients
    var defs = svg.patternify({
      tag: 'defs',
      selector: 'defs-element'
    });

    // Add Shadows
    var filter = defs
      .patternify({
        tag: 'filter',
        selector: 'shadow-filter-element'
      })
      .attr('id', dropShadowId)
      .attr('y', `${filterY}%`)
      .attr('x', `${filterX}%`)
      .attr('height', `${filterHeight}%`)
      .attr('width', `${filterWidth}%`);
    filter
      .patternify({
        tag: 'feGaussianBlur',
        selector: 'feGaussianBlur-element'
      })
      .attr('in', 'SourceAlpha')
      .attr('stdDeviation', feGaussianBlurStdDeviation)
      .attr('result', 'blur');
    filter
      .patternify({
        tag: 'feOffset',
        selector: 'feOffset-element'
      })
      .attr('in', 'blur')
      .attr('result', 'offsetBlur')
      .attr('dx', feOffsetDx)
      .attr('dy', feOffsetDy)
      .attr('x', feOffsetX)
      .attr('y', feOffsetY);
    filter
      .patternify({
        tag: 'feFlood',
        selector: 'feFlood-element'
      })
      .attr('in', 'offsetBlur')
      .attr('flood-color', color)
      .attr('flood-opacity', opacity)
      .attr('result', 'offsetColor');

    filter
      .patternify({
        tag: 'feComposite',
        selector: 'feComposite-element'
      })
      .attr('in', 'offsetColor')
      .attr('in2', 'offsetBlur')
      .attr('operator', 'in')
      .attr('result', 'offsetBlur');
    var feMerge = filter.patternify({
      tag: 'feMerge',
      selector: 'feMerge-element'
    });
    feMerge
      .patternify({
        tag: 'feMergeNode',
        selector: 'feMergeNode-blur'
      })
      .attr('in', 'offsetBlur');
    feMerge
      .patternify({
        tag: 'feMergeNode',
        selector: 'feMergeNode-graphic'
      })
      .attr('in', 'SourceGraphic');
  }

  createTree() {
    const {
      data,
      isTree,
      calc: { radius }
    } = this.getState();
    let tree = d3.cluster().size([2 * Math.PI, radius - 100]);

    if (isTree) {
      tree = d3
        .tree()
        .size([2 * Math.PI, radius - 100])
        .separation((a, b) => (a.parent == b.parent ? 1 : 3) / a.depth);
    }

    const root = tree(
      d3.hierarchy(data).sort((a, b) => d3.ascending(a.data.name, b.data.name))
    );

    this.setState({ tree, root });
  }

  data(data, initialDepth = 1) {
    const h = d3
      .hierarchy(data)
      .each(d => (d.data.id = d.data.id || this.createId()))
      .sum(d => {
        // if (d.children) return 0;
        return 1;
      });

    h.each(d => {
      if (d.data.name) {
        if (d.data.name.length > 30) {
          d.data._name = `(${d.value - 1})</br>` + d.data.name;
        } else {
          d.data._name = d.data.name + `</br>(${d.value - 1})`;
        }

        if (d.value - 1 == 0) {
          d.data._name = d.data.name;
        }
      }
      if (d.data.title) {
        d.data._name = d.data.title;
      }
    });

    h.each(d => {
      if (d.depth >= initialDepth) {
        d.data._children = d.data.children;
        d.data.children = null;
      }
    });
    this.setState({ data });
    return this;
  }

  drawNodes({
    dropShadowId,
    firstRender,
    transitionEase,
    duration,
    chart,
    tip,
    root,
    nodesWrapper,
    nodesData,
    source,
    rootCircleRadius,
    normalCircleRadius,
    svgWidth
  }) {
    const that = this;
    // Create node wrappers selection
    const nodes = nodesWrapper
      .selectAll('.node-wrapper')
      .data(nodesData, d => d.data.id || d.data.name);

    // Get enter selection
    const nodeEnter = nodes
      .enter()
      .append('g')
      .attr('class', 'node-wrapper')
      //.attr('opacity', 0)
      //     .attr(
      //         "transform",
      //         d => `
      //   rotate(${(d.x * 180) / Math.PI - 90})
      //   translate(${d.y},0)
      // `
      //     )
      .on('mouseenter.tip', function(event, d) {
        const element = d3.select(this).select('circle');
        if ((d.data.name || d.data.title).length > 27) {
          tip.show(event, d, element.node());
        }
      })
      .on('mouseleave.tip', tip.hide)
      .on('mouseenter.raise', function(d) {
        d3.select(this).raise();
      })
      .on('mouseenter', (event, d) =>
        this.handleNodeMouseEnter({ event, d, state: this.getState() })
      )
      .on('mouseleave', (event, d) =>
        this.handleNodeMouseLeave({ event, d, state: this.getState() })
      )
      .on('click', (event, d) => {
        this.handleNodeClick({ event, d, state: this.getState() });
      });

    if (firstRender) {
      nodeEnter.attr(
        "transform",
        d => `
                  rotate(${(d.x * 180) / Math.PI - 90})
                  translate(${d.y},0)
                `
      );
    }

    nodeEnter.each(function(gd) {
      d3.select(this)
        .append('circle')
        .attr('class', 'node-circle')
        .attr("fill", "black")
        .attr('r', d => {
          if (d.depth == 0) return rootCircleRadius;
          if (d.data.children || d.data._children) return normalCircleRadius;
          return 10;
        });
    });

    // Merge and get update selection
    var nodeUpdate = nodeEnter.merge(nodes);

    nodeUpdate.each(function(gd) {
      d3.select(this)
        .patternify({
          tag: 'foreignObject',
          selector: 'text-fo',
          data: d => [gd]
        })
        .attr('width', d => Math.max((normalCircleRadius - 1) * 2, 0))
        .attr('height', d => Math.max(31 * 2, 0))
        .attr('pointer-events', 'none')
        .attr('x', d => {
          if (svgWidth < 850) {
            if (d.data.children || d.data._children) return -normalCircleRadius;
            if (d.x >= Math.PI) return -(normalCircleRadius - 2) * 2 - 17;
            return 16;
          }
          if (d.data.children || d.data._children)
            return -normalCircleRadius + 1;
          if (d.x >= Math.PI) return -(normalCircleRadius - 2) * 2 - 15;
          return 16;
        })
        .attr('y', d => {
          if (svgWidth < 850) {
            return -normalCircleRadius;
          }
          return -(normalCircleRadius - 3) + 2;
        })
        .patternify({
          tag: 'xhtml:div',
          selector: 'node-text-div',
          data: d =>
            [d].map(d => {
              let _textAlign = d.x >= Math.PI ? 'end' : 'start';
              if (d.data.children || d.data._children) {
                _textAlign = 'middle';
              }

              return Object.assign(d, { _textAlign });
            })
        })
        .style('color', d => d.data.textFill)
        .style('font-size', 9 + 'px')
        .style('text-align', 'center')
        .style('line-height', 1.2)
        .html(
          d => `
                            <div style="color:${
                              d.data.children || d.data._children
                                ? 'white'
                                : 'black'
                            };display: table; width:${Math.max(
            (normalCircleRadius - 1) * 2,
            0
          )}px; height: ${31 * 2 - 4}px; overflow: hidden;">
                               <div style="display: table-cell; vertical-align: middle;text-align:${
                                 d._textAlign
                               }">
                                 <div style="opacity:1;${
                                   !(d.data.children || d.data._children)
                                     ? 'text-overflow: ellipsis;width:65px; white-space: nowrap; overflow: hidden;'
                                     : ''
                                 }" class="node-name-wrapper">
                                 ${that.limit(
                                   d.data._name || '',
                                   d.data.children || d.data._children
                                     ? svgWidth < 850
                                       ? 30
                                       : 40
                                     : 20
                                 )}
                                 </div>
                               </div>
                       </div>`
        );
    });

    nodeUpdate
      .select('foreignObject')
      .attr('transform', d => ` rotate(${(-d.x * 180) / Math.PI + 90})`);

    nodeUpdate.selectAll('.node-circle').style('filter', null);

    // Transition to the proper position for the node
    nodeUpdate
      .transition()
      .duration(duration)
      .ease(transitionEase)
      .attr('opacity', 1)
      .attr(
        "transform",
        d => `
          rotate(${(d.x * 180) / Math.PI - 90})
          translate(${d.y},0)
        `
      )
      .on('end', d => {
        nodeUpdate
          .selectAll('.node-circle')
          .style('filter', `url(#drop-shadow-id)`);
      });

    // Remove any exiting nodes
    var nodeExit = nodes
      .exit()
      .transition()
      .duration(0)
      .ease(transitionEase)
      .attr('opacity', 0)
      .attr(
        "transform",
        d => `
          rotate(${(d.x * 180) / Math.PI - 90})
          translate(${d.y},0)
        `
      )
      .remove()
      .selectAll('circle')
      .attr('r', 0);
    this.setState({ svgNodeGroups: chart.selectAll('.node-wrapper') });
  }

  drawLinks({
    initialLinkColor,
    transitionEase,
    duration,
    chart,
    root,
    linksWrapper,
    linksData,
    source
  }) {
    // Link paths selection
    const links = linksWrapper
      .selectAll('path.link')
      .data(linksData, d => d.target.data.id);

    // Get and draw enter selection
    var linkEnter = links
      .enter()
      .append('path')
      .attr('class', 'link')
      //.attr('opacity', 0);

      .attr("d", d =>
        d3
          .linkRadial()
          .angle(d => d.x)
          .radius(d => d.y)({ source: d.source, target: d.source })
      );

    // Get and draw update selection
    const linkUpdate = linkEnter
      .merge(links)
      .attr("fill", "none")
      .attr("stroke", initialLinkColor)
      .attr("stroke-opacity", 0.4)
      .attr("stroke-width", 1.5)
      .classed('svg-links', true)
      .attr('pointer-events', 'none');

    // Transition back to the parent element position
    linkUpdate
      .transition()
      .duration(duration)
      .ease(transitionEase)
      .attr('opacity', 1)
      .attr("d", d =>
        d3
          .linkRadial()
          .angle(d => d.x)
          .radius(d => d.y)(d)
      );

    // Remove any exiting links
    var linkExit = links
      .exit()
      .transition()
      .ease(transitionEase)
      .duration(0)
      // .attr('opacity', 0)

      .attr("d", d =>
        d3
          .linkRadial()
          .angle(d => d.x)
          .radius(d => d.y)(d)
      )
      .remove();

    this.setState({ svgLinks: linkUpdate });
  }

  drawSvgAndWrappers() {
    const {
      container,
      svgHeight,
      defaultFont,
      svgWidth,
      calc
    } = this.getState();

    const { centerX, centerY } = calc;

    // Draw SVG
    const svg = container
      .patternify({
        tag: 'svg',
        selector: 'svg-chart-container'
      })

      .style('overflow', 'visible')
      .attr('preserveAspectRatio', 'xMidYMid meet')
      // .attr('height', svgHeight)
      // .attr('width', svgWidth)
      .attr('font-family', defaultFont);

    svg
      .patternify({ tag: 'rect', selector: 'boundary-rect' })
      .attr('width', svgWidth)
      .attr('height', svgHeight)
      .attr('fill', 'none');

    /* Initialize tooltip */
    const tip = d3
      .tip()
      .offset([-80, 0])
      .attr('class', 'd3-tip')
      .html((EVENT, d) => d.data.name || d.data.title);

    /* Invoke the tip in the context of your visualization */
    svg.call(tip);

    //Add container g element
    var innerWrap = svg
      .patternify({
        tag: 'g',
        selector: 'inner-wrapper'
      })
      .attr(
        'transform',
        'translate(' + calc.chartLeftMargin + ',' + calc.chartTopMargin + ')'
      );

    //Add container g element
    var chart = innerWrap
      .patternify({
        tag: 'g',
        selector: 'chart'
      })
      .attr('transform', 'translate(' + centerX + ',' + centerY + ')');

    // Draw link and node wrappers
    const linksWrapper = chart.patternify({
      tag: 'g',
      selector: 'links-wrapper'
    });
    const nodesWrapper = chart.patternify({
      tag: 'g',
      selector: 'nodes-wrapper'
    });

    this.setState({ chart, svg, linksWrapper, nodesWrapper, tip });
  }

  handleNodeClick({ event, d, state: { svgCircles, svgLinks, tip, root } }) {
    tip.hide();

    d.eachAfter(ch => {
      if (d.data.children && ch.data.children && d != ch) {
        ch.data._children = ch.data.children;
        ch.data.children = null;
      }
    });

    if (d.data.children) {
      d.data._children = d.data.children;
      d.data.children = null;
    } else {
      d.data.children = d.data._children;
      d.data._children = null;
      this.limitMaximumVisibleNodes({
        max: 40,
        node: d,
        root: root
      });
    }
    this.setState({ source: d });
    this.updateTree(this.getState());
  }

  handleNodeMouseEnter({
    event,
    d: nodeData,
    state: { svgNodeGroups, svgLinks, onNodeMouseEnter }
  }) {
    onNodeMouseEnter(nodeData);
    let highlight = [];
    let ancestors = nodeData.ancestors();
    let descendants = nodeData.descendants();
    if (nodeData.depth == 0) {
      highlight = ancestors;
    } else {
      highlight = ancestors.concat(descendants);
    }

    svgLinks
      .filter(d => highlight.includes(d.target))
      .raise()
      .attr('stroke', '#1493C8')
      .classed('active', true)
      .attr('stroke-width', 5);

    svgNodeGroups
      .filter(d => highlight.includes(d))
      .classed('active', true)
      .raise()
      .attr('cursor', d =>
        d.data.children || d.data._children
          ? 'pointer'
          : Math.round(Math.random())
          ? 'no-drop'
          : 'copy'
      )
      .select('circle')
      .attr('fill', '#1493C8')

      .transition()
      .duration(500)
      .ease(d3.easeElastic)
      .attr('r', function(d) {
        if (d == nodeData) return +d3.select(this).attr('r') + 5;
        return d3.select(this).attr('r');
      });

    const template = {
      fill: 'white',
      stroke: '#2E2C2C'
    };
    const nodes = ancestors.map(d =>
      Object.assign({}, template, {
        text: d.data.name || d.data.title,
        node: d
      })
    );
  }

  handleNodeMouseLeave({
    event,
    d,
    state: {
      initialLinkColor,
      svgNodeGroups,
      svgLinks,
      normalCircleRadius,
      rootCircleRadius,
      onNodeMouseLeave
    }
  }) {
    onNodeMouseLeave(d);
    svgLinks
      .classed('active', false)
      .attr('stroke', initialLinkColor)
      .attr('stroke-width', 1.5);
    svgNodeGroups
      .classed('active', false)
      .attr('cursor', 'auto')
      .select('circle')
      .attr('fill', 'black')
      .transition()
      .ease(d3.easeElastic)
      .duration(500)
      .attr('r', d => {
        if (d.depth == 0) return rootCircleRadius;
        if (d.data.children || d.data._children) return normalCircleRadius;
        return 10;
      });
  }

  limit(string, number) {
    var points = '';
    var diff = 0;
    if (number < string.length) {
      points = '...';
      diff = 3;
    }
    string = string.slice(0, number - diff);
    return string + points;
  }

  limitMaximumVisibleNodes({ max, node, root }) {
    let { slowDuration } = this.getState();
    const descendants = root.descendants();

    let currentNodeCount =
      descendants.length + (node.data.children || []).length;
    if (currentNodeCount >= max) {
      const ancestors = node.ancestors();
      root.eachAfter(ch => {
        if (ch.data.children && !ancestors.includes(ch)) {
          ch.data._children = ch.data.children;
          ch.data.children = null;
        }
      });
      this.setState({ duration: slowDuration });
    }
  }

  updateData(data) {
    const attrs = this.getChartState();
    return this;
  }

  // Update tree chart
  updateTree() {
    const state = this.getState();

    // Compute new nodes and links data
    this.createTree(state);
    this.computeNodeAndLinkData(state);
    this.drawNodes(state);
    this.drawLinks(state);
    this.setState({ duration: state.initialDuration });
  }

  open(hierarchicalNode) {
    const state = this.getState();

    hierarchicalNode.ancestors().forEach(parent => {
      if (parent.data._children && parent != hierarchicalNode) {
        parent.data.children = parent.data._children;
        parent.data._children = null;
      }
    });

    // Compute new nodes and links data
    this.createTree(state);

    const node = state.root
      .descendants()
      .filter(d => d.data == hierarchicalNode.data)[0];

    this.limitMaximumVisibleNodes({
      max: 40,
      node: node,
      root: state.root
    });

    this.setState({ source: state.root });
    this.updateTree(this.getState());

    const nodeNew = state.root
      .descendants()
      .filter(d => d.data == hierarchicalNode.data)[0];

    this.handleNodeMouseLeave({
      event: null,
      d: nodeNew,
      state: this.getState()
    });
    this.handleNodeMouseEnter({
      event: null,
      d: nodeNew,
      state: this.getState()
    });
  }

  loopOver(root, callback) {
    if (root.children) {
      root.children.forEach(d => {
        this.loopOver(d, callback);
      });
    }
    if (root._children) {
      root._children.forEach(d => {
        this.loopOver(d, callback);
      });
    }
    callback(root);
  }
}

data = require('books.json'); //FileAttachment("books.json").json()

chart = new NetworkChart()
  .container(chartContainer)
  .svgWidth(975)
  .svgHeight(975)
  .data(data)
  .render()
