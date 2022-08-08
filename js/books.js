// import * as d3 from "d3";

const data = {
  name: "books",
  children: [
    {
      name: "2022",
      children: [
        {
          name: "audio",
          children: [
            { name: '"This Is Your Mind on Plants", Michael Pollan', value: "This-Your-Mind-Plants/dp/B08RF1K2LD" },
            { name: '"Noise: A Flaw in Human Judgment", Daniel Kahneman', value: "Noise-Human-Judgment-Daniel-Kahneman/dp/0316451401" },
            { name: '"Emperor Mollusk Versus the Sinister Brain", A. Lee Martinez', value: "Emperor-Mollusk-Versus-Sinister-Brain/dp/B007H3JHGG" }
          ]
        },
        {
          name: "physical",
          children: [
            { name: '"Her Body and Other Parties", Carmen Maria Machado', value: "Her-Body-Other-Parties-Stories/dp/155597788X" },
            { name: '"The Song of Achilles", Madeline Miller', value: "Song-Achilles-Novel-Madeline-Miller/dp/0062060627" },
            { name: '"Man\'s 4th Best Hospital", Samuel Shem', value: "Mans-4th-Best-Hospital-audiobook/dp/B07ZS414F2" },
            { name: '"Entangled Life", Merlin Sheldrake', value: "Entangled-Life-Worlds-Change-Futures/dp/052551032X" },
            { name: '"The Happiness Equation", Neil Pasricha', value: "Happiness-Equation-Nothing-Anything-Everything/dp/0425277984" }
          ]
        }
      ]
    },
    {
      name: "2021",
      children: [
        {
          name: "audio",
          children: [
            { name: '"The Spies of Warsaw", Alan Furst', value: "Spies-Warsaw-Novel-Alan-Furst/dp/0812977378" },
            { name: '"A Conjuring of Light", V. E. Schwab', value: "Conjuring-Light-Novel-Shades-Magic/dp/0765387476" },
            { name: '"A Dead Djinn in Cairo", P. Djèlí Clark', value: "Dead-Djinn-Cairo-Tor-Com-Original-ebook/dp/B01DJ0NALI" },
            { name: '"The Midnight Library", Matt Haig', value: "Midnight-Library-Novel-Matt-Haig/dp/0525559477" },
            { name: '"The Armies of Those I Love", Ken Liu', value: "Armies-Those-I-Love/dp/B08S75Z8CR" },
            { name: '"Braving the Wilderness", Brené Brown', value: "Braving-Wilderness-Quest-Belonging-Courage/dp/0812995848" },
            { name: '"Fire on the Mountain", John N Maclean', value: "Fire-Mountain-Story-South-Canyon/dp/0061829617" },
            { name: '"Akata Witch", Nnedi Okorafor', value: "Akata-Witch-Nnedi-Okorafor/dp/0142420913" },
            { name: '"The Color of Law", Richard Rothstein', value: "Color-Law-Forgotten-Government-Segregated/dp/1631494538" },
            { name: '"Project Hail Mary", Andy Weir', value: "Project-Hail-Mary-Novel-Random/dp/0593395565" },
            { name: '"Neuroscience of Everyday Life", Sam Wang', value: "Neuroscience-of-Everyday-Life-audiobook/dp/B07PLKYG53" },
            { name: '"MEM", Bethany C. Morrow', value: "MEM-Bethany-C-Morrow/dp/1944700552" },
          ]
        },
        {
          name: "physical",
          children: [
            { name: '"One Life", Megan Rapinoe', value: "One-Life-Megan-Rapinoe/dp/1984881167" },
            { name: '"Circe", Madeline Miller', value: "Circe-Madeline-Miller/dp/0316556327" },
            { name: '"Homegoing", Yaa Gyasi', value: "Homegoing-Yaa-Gyasi/dp/1101971061" },
            { name: '"The House in the Cerulean Sea", TJ Klune', value: "House-Cerulean-Sea-TJ-Klune/dp/1250217288" },
            { name: '"Whitewalling: Art, Race & Protest in 3 Acts", Aruna D\'Souza', value: "Whitewalling-Art-Race-Protest-Acts/dp/1943263140" },
            { name: '"Why Women Have Better Sex Under Socialism", Kristen R. Ghodsee', value: "Women-Have-Better-Under-Socialism/dp/1645036367" },
            { name: '"The Invisible Life of Addie LaRue", V. E. Schwab', value: "Invisible-Life-Addie-LaRue/dp/0765387565" },
            { name: '"Partners in Crime", Agatha Christie', value: "Partners-Crime-Tuppence-Agatha-Christie/dp/0062074369" },
            { name: '"Crying in H Mart", Michelle Zauner', value: "Crying-Mart-Memoir-Michelle-Zauner/dp/0525657746" },
            { name: '"Eleanor Oliphant is Completely Fine", Gail Honeyman', value: "Eleanor-Oliphant-Completely-Fine-Novel/dp/0735220697" },
            { name: '"The Mothers", Brit Bennett', value: "Mothers-Novel-Brit-Bennett/dp/039918452X" },
            { name: '"The Golden Spruce", John Vaillant', value: "John-Vaillant-Golden-Madness-Paperback/dp/B01FOD91R0" }
          ]
        }
      ]
    }
  ]
}

// Copyright 2022 Observable, Inc.
// Released under the ISC license.
// https://observablehq.com/@d3/radial-tree
function Tree(data, { // data is either tabular (array of objects) or hierarchy (nested objects)
  path, // as an alternative to id and parentId, returns an array identifier, imputing internal nodes
  id = Array.isArray(data) ? d => d.id : null, // if tabular data, given a d in data, returns a unique identifier (string)
  parentId = Array.isArray(data) ? d => d.parentId : null, // if tabular data, given a node d, returns its parent’s identifier
  children, // if hierarchical data, given a d in data, returns its children
  tree = d3.tree, // layout algorithm (typically d3.tree or d3.cluster)
  separation = tree === d3.tree ? (a, b) => (a.parent == b.parent ? 1 : 2) / a.depth : (a, b) => a.parent == b.parent ? 1 : 2,
  sort, // how to sort nodes prior to layout (e.g., (a, b) => d3.descending(a.height, b.height))
  label, // given a node d, returns the display name
  title, // given a node d, returns its hover text
  link, // given a node d, its link (if any)
  linkTarget = "_blank", // the target attribute for links (if any)
  width = 1000, //640 outer width, in pixels
  height = 1000, //400 outer height, in pixels
  margin = 10, // shorthand for margins
  marginTop = margin, // top margin, in pixels
  marginRight = margin, // right margin, in pixels
  marginBottom = margin, // bottom margin, in pixels
  marginLeft = margin, // left margin, in pixels
  radius = Math.min(width - marginLeft - marginRight, height - marginTop - marginBottom) / 2, // outer radius
  r = 3, // radius of nodes
  padding = 1, // horizontal padding for first and last column
  fill = "#999", // fill for nodes
  fillOpacity, // fill opacity for nodes
  stroke = "#555", // stroke for links
  strokeWidth = 1.5, // stroke width for links
  strokeOpacity = 0.4, // stroke opacity for links
  strokeLinejoin, // stroke line join for links
  strokeLinecap, // stroke line cap for links
  halo = "#fff", // color of label halo 
  haloWidth = 3, // padding around the labels
} = {}) {
  
  // If id and parentId options are specified, or the path option, use d3.stratify
  // to convert tabular data to a hierarchy; otherwise we assume that the data is
  // specified as an object {children} with nested objects (a.k.a. the “flare.json”
  // format), and use d3.hierarchy.
  const root = path != null ? d3.stratify().path(path)(data)
      : id != null || parentId != null ? d3.stratify().id(id).parentId(parentId)(data)
      : d3.hierarchy(data, children);

  // Sort the nodes.
  if (sort != null) root.sort(sort);

  // Compute labels and titles.
  const descendants = root.descendants();
  const L = label == null ? null : descendants.map(d => label(d.data, d));

  // Compute the layout.
  tree().size([2 * Math.PI, radius]).separation(separation)(root);

  const svg = d3.select("#books")
      // Container class to make it responsive.
      .classed("svg-container", true) 
      .append("svg")
      // Responsive SVG needs these 2 attributes and no width and height attr.
      .attr("preserveAspectRatio", "xMinYMin meet")
      .attr("viewBox", "0 0 600 400")
     // Class to make it responsive.
      .classed("svg-content-responsive", true)
//       .attr("viewBox", [-marginLeft - radius, -marginTop - radius, width, height])
//       .attr("width", width)
//       .attr("height", height)
//       .attr("style", "max-width: 100%; height: auto; height: intrinsic;")
      .attr("font-family", "sans-serif")
      .attr("font-size", 12);

  svg.append("g")
      .attr("fill", "none")
      .attr("stroke", stroke)
      .attr("stroke-opacity", strokeOpacity)
      .attr("stroke-linecap", strokeLinecap)
      .attr("stroke-linejoin", strokeLinejoin)
      .attr("stroke-width", strokeWidth)
    .selectAll("path")
    .data(root.links())
    .enter().append("path")
      .attr("d", d3.linkRadial()
          .angle(d => d.x)
          .radius(d => d.y));

  const node = svg.append("g")
    .selectAll("a")
    .data(root.descendants())
    .enter().append("a")
      .attr("xlink:href", link == null ? null : d => link(d.data, d))
      .attr("target", link == null ? null : linkTarget)
      .attr("transform", d => `rotate(${d.x * 180 / Math.PI - 90}) translate(${d.y},0)`);

  node.append("circle")
      .attr("fill", d => d.children ? stroke : fill)
      .attr("r", r);

  if (title != null) node.append("title")
      .text(d => title(d.data, d));

  if (L) node.append("text")
      .attr("transform", d => `rotate(${d.x >= Math.PI ? 180 : 0})`)
      .attr("dy", "0.32em")
      .attr("x", d => d.x < Math.PI === !d.children ? 6 : -6)
      .attr("text-anchor", d => d.x < Math.PI === !d.children ? "start" : "end")
      .attr("paint-order", "stroke")
      .attr("stroke", halo)
      .attr("stroke-width", haloWidth)
      .text((d, i) => L[i]);

  return svg.node();
}

chart = Tree(data, {
  label: d => d.name,
  title: (d, n) => `${n.ancestors().reverse().map(d => d.data.name).join(".")}`, // hover text
  link: (d, n) => `https://www.amazon.com/${n.children ? "tree" : "blob"}/master/flare/src/${n.ancestors().reverse().map(d => d.data.name).join("/")}${n.children ? "" : ".as"}`,
//   width: 1152,
//   height: 1152,
//   margin: 300
})
