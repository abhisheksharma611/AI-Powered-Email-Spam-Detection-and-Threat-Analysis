/* System architecture - clean 4-layer flow, mirrors architecture-diagram.html.
   Run: $env:NODE_PATH="<temp>\node_modules"; node build_arch_slide.js */
const pptxgen = require("pptxgenjs");

async function main() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10 x 5.625 - MUST be set before addSlide
  pres.author = "Spam Protection";
  pres.title = "System Architecture - Spam Protection Webapp";

  const INK = "1F1F1F", GRAY = "808080", BLUE = "2E5AA8";
  const BG = "F2F2F2", WHITE = "FFFFFF", STORE = "F0F0F0", EXT = "F4F4F4", FOCAL = "FFF5F6", FOCALL = "A12556";
  const DARK = "1D1333", TITLEC = "E0507C", SQ = "A12556", BLACK = "111111";
  const slide = pres.addSlide();
  slide.background = { color: BG };
  try { slide.transition = { type: "fade", speed: "medium" }; } catch (e) {}

  slide.addShape("rect", { x: 0, y: 0, w: 10, h: 0.9, fill: { color: DARK }, line: { color: DARK, width: 1 } });
  slide.addShape("rect", { x: 9.3, y: 0, w: 0.7, h: 0.55, fill: { color: SQ }, line: { color: SQ, width: 1 } });
  slide.addText("SYSTEM ARCHITECTURE", { x: 0.6, y: 0.12, w: 8.4, h: 0.6, fontFace: "Arial", fontSize: 28, bold: true, color: TITLEC, margin: 0, valign: "middle" });

  // Layer tags
  [["CLIENT", 1.28], ["SERVER", 2.03], ["AI LAYER", 2.9], ["DATA", 3.72]].forEach((t) => {
    slide.addText(t[0], { x: 0.3, y: t[1], w: 1.0, h: 0.24, fontFace: "Arial", fontSize: 10, color: "555555", margin: 0, valign: "middle" });
  });

  // Boxes: [x,y,w,h,fill,lineColor,lineW,title,tSize,sub,subSize]
  const boxes = [
    [3.5, 1.05, 3.0, 0.5, WHITE, INK, 1.5, "Browser UI", 13, "dashboard · results · charts + progress", 8],
    [3.5, 1.75, 3.0, 0.6, WHITE, INK, 1.5, "Flask Server (app.py)", 13, "auth guard · rate limits · sync + background", 8],
    [3.5, 2.55, 3.0, 0.75, FOCAL, FOCALL, 1.75, "AI + Risk Pipeline", 13, "fetch → clean → vote → score · 90/10 · 0–100", 8],
    [3.5, 3.5, 3.0, 0.5, STORE, "555555", 1.5, "SQLite Storage", 13, "email · reputation · keywords · tokens", 8],
    [7.0, 1.75, 2.1, 0.6, EXT, BLUE, 1.5, "Google Services", 12, "OAuth login · Gmail API", 8],
    [7.0, 2.55, 2.1, 0.6, EXT, GRAY, 1.25, "NVIDIA NIM", 12, "one sentence · skips silently", 8],
  ];
  boxes.forEach((b, i) => {
    const opts = { x: b[0], y: b[1], w: b[2], h: b[3], fill: { color: b[4] }, rectRadius: 0.08 };
    opts.line = i === 5 ? { color: b[5], width: b[6], dashType: "dash" } : { color: b[5], width: b[6] };
    slide.addShape("roundRect", opts);
    slide.addText(
      [{ text: b[7], options: { fontFace: "Arial", fontSize: b[8], bold: true, color: INK, breakLine: true } },
       { text: b[9], options: { fontFace: "Arial", fontSize: b[10], color: "555555" } }],
      { x: b[0], y: b[1] + 0.04, w: b[2], h: b[3] - 0.06, align: "center", valign: "middle", margin: 0 }
    );
  });

  // Connectors: thin bars + triangle heads (all positive sizes)
  const barV = (x, y1, y2, color) => slide.addShape("rect", { x: x - 0.017, y: Math.min(y1, y2), w: 0.035, h: Math.abs(y2 - y1), fill: { color }, line: { color, width: 1 } });
  const barH = (x1, x2, y, color) => slide.addShape("rect", { x: Math.min(x1, x2), y: y - 0.017, w: Math.abs(x2 - x1), h: 0.035, fill: { color }, line: { color, width: 1 } });
  const head = (cx, tipY, dir, color) => {
    // isosceles triangle preset points up; rotate to aim
    const rot = dir === "down" ? 180 : dir === "right" ? 90 : dir === "left" ? 270 : 0;
    slide.addShape("triangle", { x: cx - 0.065, y: dir === "down" ? tipY - 0.11 : tipY, w: 0.13, h: 0.11, fill: { color }, line: { color, width: 1 }, rotate: rot });
  };
  const CX = 5.0;
  barV(CX, 1.55, 1.75, INK); head(CX, 1.75, "down", INK);
  slide.addText("HTTPS", { x: CX + 0.08, y: 1.58, w: 0.7, h: 0.2, fontFace: "Arial", fontSize: 8, color: "555555", margin: 0 });
  barH(6.5, 7.0, 2.05, BLUE); head(7.0, 2.05, "right", BLUE); head(6.5, 2.05, "left", BLUE);
  slide.addText("login+fetch", { x: 6.45, y: 1.78, w: 1.1, h: 0.2, fontFace: "Arial", fontSize: 8, color: BLUE, align: "center", margin: 0 });
  barV(CX, 2.35, 2.55, INK); head(CX, 2.55, "down", INK);
  barV(CX, 3.3, 3.5, INK); head(CX, 3.5, "down", INK);
  slide.addText("records", { x: CX + 0.08, y: 3.33, w: 0.7, h: 0.2, fontFace: "Arial", fontSize: 8, color: "555555", margin: 0 });
  // return reads (left elbow, gray): up from SQLite to Flask
  barV(3.1, 2.05, 3.75, GRAY); barH(3.1, 3.5, 3.75, GRAY); barH(3.1, 3.5, 2.05, GRAY); head(3.3, 2.05, "up", GRAY);
  slide.addText("reads", { x: 2.35, y: 2.8, w: 0.6, h: 0.2, fontFace: "Arial", fontSize: 8, color: GRAY, align: "center", margin: 0 });
  // optional NIM tap
  barH(6.5, 7.0, 2.85, GRAY); head(7.0, 2.85, "right", GRAY);
  slide.addText("optional", { x: 6.45, y: 2.9, w: 1.1, h: 0.2, fontFace: "Arial", fontSize: 8, color: GRAY, align: "center", margin: 0 });

  slide.addText("sync + background scans share one pipeline · blue = Google · gray dashed = optional", {
    x: 0.5, y: 4.25, w: 9.0, h: 0.26, fontFace: "Arial", fontSize: 9, color: "333333", align: "center", margin: 0, valign: "middle",
  });
  slide.addShape("rect", { x: 0, y: 5.25, w: 10, h: 0.375, fill: { color: BLACK }, line: { color: BLACK, width: 1 } });
  slide.addShape("rect", { x: 9.3, y: 5.25, w: 0.7, h: 0.375, fill: { color: SQ }, line: { color: SQ, width: 1 } });

  slide.addNotes("SAY (45s): Browser talks only to Flask. Flask logs you in via Google, fetches 50 mails, and runs one shared pipeline — sync scans and background tasks call the same function. The pipeline votes with AI, adjusts to 0–100 risk, saves to local SQLite, and Flask reads it back for the pages. If asked about scale: single worker, CPU 200–500ms per mail.");
  await pres.writeFile({ fileName: "C:\\Users\\dell\\Desktop\\Spam Protection 5\\Architecture-One-Slide.pptx" });
  console.log("WROTE Architecture-One-Slide.pptx");
}

main().catch((e) => { console.error("BUILD FAILED:", e); process.exit(1); });
