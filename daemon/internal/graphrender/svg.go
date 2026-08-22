package graphrender

import (
	"fmt"
	"math"
	"path"
	"strings"
)

// SVG, because the scorecard it lands in is a markdown file in the vault and the
// vault is read in Obsidian and in a browser. Both draw SVG; neither runs a
// plotting library. It also diffs — a render that changed is a readable change
// rather than a different blob of pixels.

// classPalette is fixed and ordered, so a class keeps its colour between runs.
// A palette assigned by iteration order would recolour the whole picture the day
// a new class appeared, and the reader would see a change that did not happen.
var classPalette = map[string]string{
	"semantic":     "#4C7FB8",
	"procedural":   "#5B9E6A",
	"episodic":     "#B8894C",
	"entities":     "#8A6BB0",
	"crystallized": "#C05B6B",
	"mocs":         "#4FA0A8",
}

// unfiledColour is for a note whose class nothing can state yet.
//
// Deliberately drab, and deliberately present. Most of this corpus is unfiled
// today because filing is what this whole arc builds and enrichment is off, so
// the grey mass *is* the finding — colouring it in by guessing from its path
// would hide the one thing the picture has to say right now.
const unfiledColour = "#9AA0A6"

// UnfiledClass is the label for a note with no class yet.
const UnfiledClass = "unfiled"

func colourFor(class string) string {
	if c, ok := classPalette[class]; ok {
		return c
	}
	return unfiledColour
}

// SVG renders the settled layout.
//
// Everything is rounded to two decimals on the way out. Float noise in the
// sixteenth digit is invisible in a picture and fatal to a byte-comparison, and
// the byte-comparison is how this render's determinism is checked.
func (l Layout) SVG(width, height int) string {
	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="memory context graph">`,
		width, height, width, height)
	b.WriteString(`<rect width="100%" height="100%" fill="#FBFBFA"/>`)

	if len(l.Nodes) == 0 {
		// An empty corpus renders an empty picture that says so, rather than a
		// blank rectangle the reader has to interpret.
		fmt.Fprintf(&b, `<text x="%d" y="%d" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#6B7280">no links in the corpus yet</text>`,
			width/2, height/2)
		b.WriteString(`</svg>`)
		return b.String()
	}

	sx, sy, ox, oy := fit(l.Nodes, width, height)

	// Edges first so nodes sit on top of them.
	b.WriteString(`<g stroke="#D8D8D4" stroke-width="0.6">`)
	for _, e := range l.Edges {
		s, t := l.Nodes[e.Source], l.Nodes[e.Target]
		fmt.Fprintf(&b, `<line x1="%s" y1="%s" x2="%s" y2="%s"/>`,
			f(s.X*sx+ox), f(s.Y*sy+oy), f(t.X*sx+ox), f(t.Y*sy+oy))
	}
	b.WriteString(`</g>`)

	b.WriteString(`<g>`)
	for _, n := range l.Nodes {
		fmt.Fprintf(&b, `<circle cx="%s" cy="%s" r="%s" fill="%s"><title>%s</title></circle>`,
			f(n.X*sx+ox), f(n.Y*sy+oy), f(radiusFor(n.Degree)),
			colourFor(n.Class), escape(label(n.Rel, n.Degree)))
	}
	b.WriteString(`</g>`)

	b.WriteString(legend(l.Classes()))
	b.WriteString(`</svg>`)
	return b.String()
}

// radiusFor sizes a hub by degree, on a square root so that a node with a
// hundred links is bigger than one with four without being twenty-five times the
// area and swallowing the picture.
func radiusFor(degree int) float64 {
	const base, scale = 2.0, 1.4
	return base + scale*math.Sqrt(float64(degree))
}

// fit scales the settled coordinates into the viewport with a margin.
//
// Uniform scale on both axes: stretching one to fill the frame would distort
// every distance the simulation just spent 300 ticks getting right.
func fit(nodes []Node, width, height int) (sx, sy, ox, oy float64) {
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	for _, n := range nodes {
		minX, maxX = math.Min(minX, n.X), math.Max(maxX, n.X)
		minY, maxY = math.Min(minY, n.Y), math.Max(maxY, n.Y)
	}
	const margin = 24.0
	w, h := float64(width)-2*margin, float64(height)-2*margin
	spanX, spanY := maxX-minX, maxY-minY
	// A single node, or a row of them, has zero span on an axis. Dividing by it
	// would place every node at infinity.
	if spanX <= 0 {
		spanX = 1
	}
	if spanY <= 0 {
		spanY = 1
	}
	s := math.Min(w/spanX, h/spanY)
	ox = margin + (w-spanX*s)/2 - minX*s
	oy = margin + (h-spanY*s)/2 - minY*s
	return s, s, ox, oy
}

func legend(classes []string) string {
	if len(classes) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString(`<g font-family="system-ui,sans-serif" font-size="11" fill="#3C4043">`)
	y := 20
	for _, c := range classes {
		fmt.Fprintf(&b, `<circle cx="16" cy="%d" r="4" fill="%s"/><text x="26" y="%d">%s</text>`,
			y, colourFor(c), y+4, escape(c))
		y += 16
	}
	b.WriteString(`</g>`)
	return b.String()
}

// label is what hovering a node shows: the note's name and how connected it is.
func label(rel string, degree int) string {
	name := strings.TrimSuffix(path.Base(rel), ".md")
	if degree == 1 {
		return name + " · 1 link"
	}
	return fmt.Sprintf("%s · %d links", name, degree)
}

// f formats a coordinate at two decimals and without a negative zero, which
// differs from positive zero as text and would break a byte-comparison over two
// runs that are otherwise identical.
func f(v float64) string {
	s := fmt.Sprintf("%.2f", v)
	if s == "-0.00" {
		return "0.00"
	}
	return s
}

// escape covers the five XML entities. A note titled `Q&A` or one with angle
// brackets in its name would otherwise produce a document that will not parse.
func escape(s string) string {
	return strings.NewReplacer(
		"&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;",
	).Replace(s)
}
