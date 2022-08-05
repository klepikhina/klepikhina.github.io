import * as d3 from "d3";

const data = {
  name: "books",
  children: [
    {
      name: "2022",
      children: [
        {
          name: "audio",
          children: [
            {
              name: '"This Is Your Mind on Plants", Michael Pollan',
              value: 3938
            },
            {
              name: '"Noise: A Flaw in Human Judgment", Daniel Kahneman',
              value: 3812
            },
            {
              name:
                '"Emperor Mollusk Versus the Sinister Brain", A. Lee Martinez',
              value: 6714
            }
          ]
        },
        {
          name: "physical",
          children: [
            { name: '"Her Body and Other Parties", Carmen Maria Machado' },
            { name: '"The Song of Achilles", Madeline Miller', value: 5731 },
            { name: '"Man\'s 4th Best Hospital", Samuel Shem', value: 7840 },
            { name: '"Entangled Life", Merlin Sheldrake', value: 5914 },
            { name: '"The Happiness Equation", Neil Pasricha', value: 3416 }
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
            { name: "", value: 1983 },
            { name: '"The Spies of Warsaw", Alan Furst', value: 2047 },
            { name: '"A Conjuring of Light", V. E. Schwab', value: 1375 },
            { name: '"A Dead Djinn in Cairo", P. Djèlí Clark', value: 8746 },
            { name: '"The Midnight Library", Matt Haig', value: 2202 },
            { name: '"The Armies of Those I Love", Ken Liu', value: 1382 },
            { name: '"Braving the Wilderness", Brené Brown', value: 1629 },
            { name: '"Fire on the Mountain", John N Maclean', value: 1675 },
            { name: '"Akata Witch", Nnedi Okorafor', value: 2042 },
            { name: '"The Color of Law", Richard Rothstein', value: 2042 },
            { name: '"Project Hail Mary", Andy Weir', value: 2042 },
            { name: '"Neuroscience of Everyday Life", The Great Courses - Professor Sam Wang', value: 2042 },
            { name: '"MEM", Bethany C. Morrow', value: 2042 },
          ]
        },
        {
          name: "physical",
          children: [
            { name: '"One Life", Megan Rapinoe', value: 1983 },
            { name: '"Circe", Madeline Miller', value: 2047 },
            { name: '"Homegoing", Yaa Gyasi', value: 1375 },
            { name: '"The House in the Cerulean Sea", TJ Klune', value: 8746 },
            {
              name:
                '"Whitewalling: Art, Race & Protest in 3 Acts", Aruna D\'Souza',
              value: 2202
            },
            {
              name:
                '"Why Women Have Better Sex Under Socialism", Kristen R. Ghodsee',
              value: 1382
            },
            {
              name: '"The Invisible Life of Addie LaRue", V. E. Schwab',
              value: 1629
            },
            { name: '"Partners in Crime", Agatha Christie', value: 1675 },
            { name: '"Crying in H Mart", Michelle Zauner', value: 2042 },
            {
              name: '"Eleanor Oliphant is Completely Fine", Gail Honeyman',
              value: 2042
            },
            { name: '"The Mothers", Brit Bennett', value: 2042 },
            { name: '"The Golden Spruce", John Vaillant', value: 2042 }
          ]
        }
      ]
    }
  ]
};

const margin = { top: 10, right: 120, bottom: 10, left: 40 };
const width = d3.width || 960;
const root = d3.hierarchy(data);
const dx = 10;
const dy = width / 6;
const tree = d3.tree().nodeSize([dx, dy]);
const diagonal = d3
  .linkHorizontal()
  .x((d) => d.y)
  .y((d) => d.x);

root.x0 = dy / 2;
root.y0 = 0;
root.descendants().forEach((d, i) => {
  d.id = i;
  d._children = d.children;
  // if (d.depth && d.data.name.length !== 7) d.children = null;
});

tree(root);

const svg = d3
  .create("books")
  .attr("viewBox", [-margin.left, -margin.top, width, dx])
  .style("font", "10px sans-serif")
  .style("user-select", "none");

const gLink = svg
  .append("g")
  .attr("fill", "none")
  .attr("stroke", "#555")
  .attr("stroke-opacity", 0.4)
  .attr("stroke-width", 1.5);

const gNode = svg
  .append("g")
  .attr("cursor", "pointer")
  .attr("pointer-events", "all");

update(root);

document.querySelector("#app").appendChild(svg.node());

function update(source) {
  const duration = d3.event && d3.event.altKey ? 2500 : 250;
  const nodes = root.descendants().reverse();
  const links = root.links();

  // Compute the new tree layout.

  let left = root;
  let right = root;
  root.eachBefore((node) => {
    if (node.x < left.x) left = node;
    if (node.x > right.x) right = node;
  });

  const height = right.x - left.x + margin.top + margin.bottom;

  const transition = svg
    .transition()
    .duration(duration)
    .attr("viewBox", [-margin.left, left.x - margin.top, width, height])
    .tween(
      "resize",
      window.ResizeObserver ? null : () => () => svg.dispatch("toggle")
    );

  // Update the nodes…
  const node = gNode.selectAll("g").data(nodes, (d) => d.id);

  // Enter any new nodes at the parent's previous position.
  const nodeEnter = node
    .enter()
    .append("g")
    .attr("transform", (d) => `translate(${source.y0},${source.x0})`)
    .attr("fill-opacity", 0)
    .attr("stroke-opacity", 0)
    .on("click", (event, d) => {
      d.children = d.children ? null : d._children;
      update(d);
    });

  nodeEnter
    .append("circle")
    .attr("r", 2.5)
    .attr("fill", (d) => (d._children ? "#555" : "#999"))
    .attr("stroke-width", 10);

  nodeEnter
    .append("text")
    .attr("dy", "0.31em")
    .attr("x", (d) => (d._children ? -6 : 6))
    .attr("text-anchor", (d) => (d._children ? "end" : "start"))
    .text((d) => d.data.name)
    .clone(true)
    .lower()
    .attr("stroke-linejoin", "round")
    .attr("stroke-width", 3)
    .attr("stroke", "white");

  // Transition nodes to their new position.
  node
    .merge(nodeEnter)
    .transition(transition)
    .attr("transform", (d) => `translate(${d.y},${d.x})`)
    .attr("fill-opacity", 1)
    .attr("stroke-opacity", 1);

  // Transition exiting nodes to the parent's new position.
  node
    .exit()
    .transition(transition)
    .remove()
    .attr("transform", (d) => `translate(${source.y},${source.x})`)
    .attr("fill-opacity", 0)
    .attr("stroke-opacity", 0);

  // Update the links…
  const link = gLink.selectAll("path").data(links, (d) => d.target.id);

  // Enter any new links at the parent's previous position.
  const linkEnter = link
    .enter()
    .append("path")
    .attr("d", (d) => {
      const o = { x: source.x0, y: source.y0 };
      return diagonal({ source: o, target: o });
    });

  // Transition links to their new position.
  link.merge(linkEnter).transition(transition).attr("d", diagonal);

  // Transition exiting nodes to the parent's new position.
  link
    .exit()
    .transition(transition)
    .remove()
    .attr("d", (d) => {
      const o = { x: source.x, y: source.y };
      return diagonal({ source: o, target: o });
    });

  // Stash the old positions for transition.
  root.eachBefore((d) => {
    d.x0 = d.x;
    d.y0 = d.y;
  });
}
