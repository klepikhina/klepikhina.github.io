chart = {
  const nodeRadius = 2.5
  const svg = d3.create("books")
  
  const gLines = svg.append("g")
    .attr("fill", "none")
    .attr("stroke", "#A7C8F2")
    .attr("stroke-opacity", 0.4)
    .attr("stroke-width", 1.5)
  const gNodes = svg.append("g")
  const gTexts = svg.append("g")
    .attr("font-family", "sans-serif")
    .attr("font-size", 10)
    .attr("fill", "#F25116")
    .attr("stroke-linejoin", "round")
    .attr("stroke-width", 3)

  const root = tree(data)
  
  // create a copy of all children
  root.descendants().forEach((d, i) => {
    d.id = i
    d._children = d.children
  })
  
  update()

  function update() {
    updateLinks()
    updateLabels()
    updateNodes()
  }
  
  function updateLinks() {
    const links = gLines
      .selectAll("line")
      .data(root.links(), d => d)

    links
      .exit().remove()

    links
      .enter().append("line")
        .attr("x1", d => radialPoint(d.source.x, d.source.y)[0])
        .attr("y1", d => radialPoint(d.source.x, d.source.y)[1])
        .attr("x2", d => radialPoint(d.target.x, d.target.y)[0])
        .attr("y2", d => radialPoint(d.target.x, d.target.y)[1])
  }
  
  function updateLabels() {
    const labels = gTexts
      .selectAll("text")
      .data(root.descendants(), d => d)

   labels
     .exit().remove()
   
   labels
     .enter().append("text")
       .attr("transform", d => `
         rotate(${d.x * 180 / Math.PI - 90}) 
         translate(${d.y},0) 
         rotate(${d.x >= Math.PI ? 180 : 0})
       `)
       .attr("dy", "0.31em")
       .attr("x", d => d.x < Math.PI === !d.children ? 6 : -6)
       .attr("text-anchor", d => d.x < Math.PI === !d.children ? "start" : "end")
       .text(d => d.data.name)
       .clone(true).lower()
       .attr("stroke", "white")
  }
  
  function updateNodes() {
    const nodes = gNodes
      .selectAll("circle")
      .data(root.descendants(), d => d)

    nodes
      .exit().remove()

    nodes
      .enter().append("circle")
        .attr("transform", d => `
          rotate(${d.x * 180 / Math.PI - 90})
          translate(${d.y},0)
        `)
        .attr("r", nodeRadius)
        .attr("fill", "#048ABF")
        .on("click", d => {
          d.children = d.children ? null : d._children
          update()
        })
        .on("mouseover", function() {
          d3.select(this).attr("r", nodeRadius * 3).attr("fill", "#027368") })
        .on("mouseout", function() {
          d3.select(this).attr("r", nodeRadius).attr("fill", "#048ABF")
        })

    nodes
      .attr("fill", "#048ABF")
  }

  return svg.attr("viewBox", autoBox).node()
}

function radialPoint(x, y) {
  return [(y = +y) * Math.cos(x -= Math.PI / 2), y * Math.sin(x)]
}

function autoBox() {
  document.body.appendChild(this)
  const {x, y, width, height} = this.getBBox()
  document.body.removeChild(this)
  return [x, y, width, height]
}

let data_source = require('../js/books.json');
data = d3.hierarchy(data_source[0]) //await FileAttachment("flare-2.json").json())
    .sort((a, b) => d3.ascending(a.data.name, b.data.name))

tree = d3.tree()
    .size([2 * Math.PI, radius])
    .separation((a, b) => (a.parent == b.parent ? 1 : 2) / a.depth)

width = 954
radius = width / 2

